//! Passive CAN sniffer with a hardcoded Toyota Prius/Auris-platform DBC.
//!
//! Listens to every broadcast CAN frame on its interface, looks the frame ID
//! up in [`PROFILE`], and emits one [`Sample`] per matched signal.
//!
//! Why a hardcoded profile: the production target is "load opendbc DBC files
//! at runtime per car", but for tonight's test the only car we can hit is a
//! Toyota Vitz DBA-NSP130 — which shares the E-platform CAN layout with the
//! Toyota Prius 2010 DBC in opendbc. Hardcoding the five frames we know
//! match (verified via candump on the Vitz, 2026-05-28) gets us a 25× rate
//! bump on RPM (≈42 Hz vs the OBD poller's ≈1.7 Hz) without waiting for the
//! generic DBC loader.
//!
//! The sniffer needs its **own** CAN socket, separate from the poller's:
//! SocketCAN sockets each receive a private copy of every frame on the
//! interface, so the two threads don't interfere. The sniffer skips frames
//! in the OBD-II address window so it doesn't double-count what the poller
//! is already decoding.

use std::thread::JoinHandle;
use std::time::Duration;

use crate::can::interface::CanError;
use crate::can::CanInterface;
use crate::extractor::sample::{Sample, SampleSource};
use crate::extractor::SampleSender;

/// One decodable signal inside a CAN frame.
///
/// Big-endian (Motorola / DBC `@0`) only — that's all the Toyota DBC uses.
/// `byte_hi` is the high byte index of the value; `byte_lo = byte_hi + 1`.
#[derive(Debug, Clone, Copy)]
struct Signal {
    name: &'static str,
    byte_hi: usize,
    length_bytes: usize,
    scale: f64,
    offset: f64,
    unit: &'static str,
}

#[derive(Debug, Clone, Copy)]
struct Message {
    id: u32,
    name: &'static str,
    signals: &'static [Signal],
}

/// Hardcoded Toyota Prius 2010 powertrain DBC subset — the frames we
/// observed on the Vitz, with their DBC-defined signal layouts.
const PROFILE: &[Message] = &[
    Message {
        id: 0x1C4,
        name: "POWERTRAIN",
        signals: &[Signal {
            name: "engine_rpm",
            byte_hi: 0,
            length_bytes: 2,
            scale: 1.0,
            offset: 0.0,
            unit: "rpm",
        }],
    },
    Message {
        id: 0x0AA,
        name: "WHEEL_SPEEDS",
        signals: &[
            Signal { name: "wheel_speed_fr", byte_hi: 0, length_bytes: 2,
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_fl", byte_hi: 2, length_bytes: 2,
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_rr", byte_hi: 4, length_bytes: 2,
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_rl", byte_hi: 6, length_bytes: 2,
                     scale: 0.0062, offset: -67.67, unit: "mph" },
        ],
    },
    Message {
        id: 0x0B4,
        name: "SPEED",
        signals: &[Signal {
            // DBC says SPEED is at 47|16@0+ which is bytes 5..6 inclusive
            // (bit 47 = byte 5 MSB).
            name: "speed",
            byte_hi: 5,
            length_bytes: 2,
            scale: 0.0062,
            offset: 0.0,
            unit: "mph",
        }],
    },
];

/// True if the frame ID falls inside the OBD-II diagnostic range — the
/// poller is already publishing those, and we don't want duplicates.
fn is_obd_window(id: u32) -> bool {
    (0x7DF..=0x7EF).contains(&id)
}

/// Spawns the sniffer on a dedicated thread. `can` is moved in.
pub fn spawn(can: CanInterface, tx: SampleSender) -> JoinHandle<()> {
    std::thread::Builder::new()
        .name("can-sniffer".into())
        .spawn(move || run(can, tx))
        .expect("spawn can-sniffer thread")
}

fn run(can: CanInterface, tx: SampleSender) {
    log::info!(
        "can-sniffer: starting · profile = Toyota Prius 2010 PT · {} message(s)",
        PROFILE.len()
    );

    let mut frames_seen: u64 = 0;
    let mut frames_decoded: u64 = 0;
    let mut last_log = std::time::Instant::now();

    loop {
        match can.recv() {
            Ok(frame) => {
                frames_seen += 1;
                if is_obd_window(frame.id) {
                    continue;
                }
                if let Some(msg) = PROFILE.iter().find(|m| m.id == frame.id) {
                    decode_and_emit(msg, &frame, &tx);
                    frames_decoded += 1;
                }
            }
            Err(CanError::Timeout) => {}
            Err(e) => {
                log::warn!("can-sniffer: recv error: {} — backing off", e);
                std::thread::sleep(Duration::from_millis(200));
            }
        }

        // Periodic stats line so the logs show whether the sniffer is alive.
        if last_log.elapsed() >= Duration::from_secs(30) {
            log::info!(
                "can-sniffer: seen={} decoded={}",
                frames_seen, frames_decoded
            );
            last_log = std::time::Instant::now();
        }
    }
}

fn decode_and_emit(msg: &Message, frame: &crate::can::CanFrame, tx: &SampleSender) {
    let data = frame.data();
    for sig in msg.signals {
        let end = sig.byte_hi + sig.length_bytes;
        if end > data.len() {
            continue;
        }
        let raw: u32 = data[sig.byte_hi..end]
            .iter()
            .fold(0u32, |acc, &b| (acc << 8) | b as u32);
        let value = raw as f64 * sig.scale + sig.offset;
        let signal = format!("dbc.toyota.{}.{}", msg.name, sig.name);
        let _ = tx.send(Sample::new(
            SampleSource::Sniffer,
            signal,
            value,
            sig.unit,
        ));
    }
}
