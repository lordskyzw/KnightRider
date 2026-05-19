//! Local HTTP + WebSocket server.
//!
//! Bound to `0.0.0.0:8080` on the Pi LAN. No TLS, no auth — open during demo
//! phase (see the `knight-rider-system-architecture` memory).
//!
//! Routes:
//!
//! | Path                   | Verb | Purpose                                            |
//! |------------------------|------|----------------------------------------------------|
//! | `/health`              | GET  | Liveness probe + device info                       |
//! | `/ws/live`             | GET  | WebSocket upgrade; streams [`Sample`] JSON         |
//! | `/backlog?since=N`     | GET  | Pi-built batches with `batch_id > N` (NDJSON)      |
//! | `/known-track/alerts`  | GET  | Recent alerts from on-Pi known-track inference     |
//! | `/inbox`               | POST | Cloud→Pi delivery from courier phones              |

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::extract::State;
use axum::response::Json;
use axum::routing::{get, post};
use axum::Router;
use serde::Serialize;

use crate::buffer::store::Store;
use crate::extractor::SampleSender;

pub mod backlog;
pub mod inbox;
pub mod known_track;
pub mod live;

/// Default bind address for the LAN server.
pub fn default_addr() -> SocketAddr {
    "0.0.0.0:8080".parse().expect("static addr parses")
}

/// Shared application state passed to every handler.
#[derive(Clone)]
pub struct AppState {
    pub samples_tx: SampleSender,
    pub store: Arc<Mutex<Store>>,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/ws/live", get(live::ws_handler))
        .route("/backlog", get(backlog::handler))
        .route("/known-track/alerts", get(known_track::alerts_handler))
        .route("/inbox", post(inbox::handler))
        .with_state(state)
}

/// Starts the axum server. Returns when the listener errors out.
pub async fn serve(state: AppState, addr: SocketAddr) -> std::io::Result<()> {
    let listener = tokio::net::TcpListener::bind(addr).await?;
    log::info!("server listening on http://{}", addr);
    axum::serve(listener, router(state)).await
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    device_id: String,
    next_batch_id: u64,
    pending_batches: u64,
}

async fn health(State(state): State<AppState>) -> Json<HealthResponse> {
    let store = state.store.clone();
    let snapshot = tokio::task::spawn_blocking(move || {
        let guard = store.lock().expect("store mutex poisoned");
        (
            guard.device_id().to_string(),
            guard.next_batch_id(),
            guard.pending_count().unwrap_or(0),
        )
    })
    .await
    .unwrap_or_else(|_| (String::new(), 0, 0));

    Json(HealthResponse {
        status: "ok",
        device_id: snapshot.0,
        next_batch_id: snapshot.1,
        pending_batches: snapshot.2,
    })
}
