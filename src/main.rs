//! Knight Rider service entrypoint.
//!
//! Runs the full pipeline: CAN extractor → broadcast channel → buffer writer
//! + WebSocket / HTTP server. The Pi is the canonical store; phones pull
//! `/backlog?since=N` and act as couriers to the cloud.
//!
//! # Usage
//!
//! ```bash
//! # Pi: production
//! knight-rider --interface can0 --buffer /var/lib/knight-rider/buffer.sqlite
//!
//! # Dev with vcan
//! knight-rider --interface vcan0
//!
//! # Override server bind
//! knight-rider --bind 0.0.0.0:9090
//! ```

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use knight_rider::buffer::store::Store;
use knight_rider::buffer::writer;
use knight_rider::can::CanInterface;
use knight_rider::extractor::{self, obd_poller, sniffer};
use knight_rider::server::{self, AppState};

struct Args {
    interface: String,
    buffer_path: PathBuf,
    bind_addr: SocketAddr,
}

impl Args {
    fn from_env() -> Self {
        let args: Vec<String> = std::env::args().collect();
        let interface = arg(&args, &["--interface", "-i"]).unwrap_or_else(|| "can0".into());
        let buffer_path = arg(&args, &["--buffer", "-b"])
            .map(PathBuf::from)
            .unwrap_or_else(default_buffer_path);
        let bind_addr = arg(&args, &["--bind"])
            .and_then(|s| s.parse().ok())
            .unwrap_or_else(server::default_addr);
        Self { interface, buffer_path, bind_addr }
    }
}

fn arg(args: &[String], flags: &[&str]) -> Option<String> {
    args.iter()
        .position(|a| flags.contains(&a.as_str()))
        .and_then(|i| args.get(i + 1))
        .cloned()
}

fn default_buffer_path() -> PathBuf {
    if cfg!(target_os = "linux") {
        PathBuf::from("/var/lib/knight-rider/buffer.sqlite")
    } else {
        PathBuf::from("./knight-rider-buffer.sqlite")
    }
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    log::info!("Knight Rider v{}", env!("CARGO_PKG_VERSION"));
    let args = Args::from_env();

    // ── CAN interface ──────────────────────────────────────────────────────
    // Two sockets on the same interface: one for the OBD poller (sends + recvs
    // diagnostic frames), one for the passive sniffer (raw broadcast read).
    // SocketCAN gives each socket its own RX queue so they don't interfere.
    log::info!("opening CAN interface: {}", args.interface);
    let can_obd = match CanInterface::open(&args.interface) {
        Ok(c) => c,
        Err(e) => {
            log::error!("failed to open CAN (obd): {}", e);
            std::process::exit(1);
        }
    };
    let can_sniff = match CanInterface::open(&args.interface) {
        Ok(c) => Some(c),
        Err(e) => {
            log::warn!("failed to open second CAN socket for sniffer: {} — running with poller only", e);
            None
        }
    };

    // ── Canonical buffer ──────────────────────────────────────────────────
    if let Some(parent) = args.buffer_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    log::info!("opening buffer: {}", args.buffer_path.display());
    let store = match Store::open(&args.buffer_path) {
        Ok(s) => s,
        Err(e) => {
            log::error!("failed to open buffer: {}", e);
            std::process::exit(1);
        }
    };
    let device_id = store.device_id();
    let next_batch = store.next_batch_id();
    let pending = store.pending_count().unwrap_or(0);
    log::info!("device_id={} next_batch_id={} pending={}", device_id, next_batch, pending);
    let store = Arc::new(Mutex::new(store));

    // ── Broadcast channel ─────────────────────────────────────────────────
    let (tx, _initial_rx) = extractor::channel();

    // Subscribe the writer BEFORE the poller starts emitting, so we don't
    // race past the first batch of samples while the task is still being
    // scheduled.
    let writer_rx = tx.subscribe();

    // ── Extractors ────────────────────────────────────────────────────────
    // Command channel into the poller for write ops (clear DTCs). Bounded — at
    // most a couple of clears in flight; the poller drains it between polls.
    let (clear_tx, clear_rx) = tokio::sync::mpsc::channel(4);
    let _poller = obd_poller::spawn(can_obd, tx.clone(), Default::default(), Some(clear_rx));
    log::info!("obd-poller spawned");

    if let Some(c) = can_sniff {
        let _sniffer = sniffer::spawn(c, tx.clone());
        log::info!("can-sniffer spawned (Toyota Prius PT + Axio body profile)");
    }

    // ── Buffer writer ─────────────────────────────────────────────────────
    let writer_handle = tokio::spawn({
        let store = store.clone();
        async move {
            writer::run(store, writer_rx, writer::WriterConfig::default()).await;
        }
    });

    // ── HTTP + WS server ──────────────────────────────────────────────────
    let serve_handle = tokio::spawn({
        let state = AppState {
            samples_tx: tx.clone(),
            store: store.clone(),
            clear_tx: Some(clear_tx),
        };
        let addr = args.bind_addr;
        async move {
            if let Err(e) = server::serve(state, addr).await {
                log::error!("server error: {}", e);
            }
        }
    });

    // ── Wait for shutdown ─────────────────────────────────────────────────
    if let Err(e) = tokio::signal::ctrl_c().await {
        log::warn!("ctrl_c handler failed: {}", e);
    }
    log::info!("shutdown signal received");

    // Drop the broadcast sender so the writer's receiver closes and flushes.
    drop(tx);
    let _ = tokio::time::timeout(Duration::from_secs(2), writer_handle).await;

    serve_handle.abort();
    log::info!("bye");
}
