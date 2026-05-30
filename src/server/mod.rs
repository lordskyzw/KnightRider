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
//! | `/clear-dtc`           | POST | OBD-II Mode 0x04 — clear DTCs (engine-off gated)   |

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use axum::routing::{get, post};
use axum::Router;
use serde::Serialize;

use crate::buffer::store::Store;
use crate::extractor::obd_poller::PollerCommand;
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
    /// Command channel into the OBD poller for write ops (clear DTCs). `None`
    /// when no poller is running (CAN unavailable), so the route 503s cleanly.
    pub clear_tx: Option<tokio::sync::mpsc::Sender<PollerCommand>>,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/ws/live", get(live::ws_handler))
        .route("/backlog", get(backlog::handler))
        .route("/known-track/alerts", get(known_track::alerts_handler))
        .route("/inbox", post(inbox::handler))
        .route("/clear-dtc", post(clear_dtc))
        .with_state(state)
}

/// `POST /clear-dtc` — OBD-II Mode 0x04. Hands the request to the poller (which
/// owns the CAN socket + applies the engine-off gate) and waits for the result.
async fn clear_dtc(State(state): State<AppState>) -> Response {
    let Some(tx) = state.clear_tx.clone() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "ok": false, "message": "CAN not available on this device" })),
        )
            .into_response();
    };
    let (reply_tx, reply_rx) = tokio::sync::oneshot::channel();
    if tx.send(PollerCommand::ClearDtcs { reply: reply_tx }).await.is_err() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "ok": false, "message": "poller not running" })),
        )
            .into_response();
    }
    match tokio::time::timeout(Duration::from_secs(4), reply_rx).await {
        Ok(Ok(Ok(msg))) => {
            (StatusCode::OK, Json(serde_json::json!({ "ok": true, "message": msg }))).into_response()
        }
        Ok(Ok(Err(msg))) => {
            // 409: a refusal we understand (engine running, ECU/gateway NRC).
            (StatusCode::CONFLICT, Json(serde_json::json!({ "ok": false, "message": msg })))
                .into_response()
        }
        _ => (
            StatusCode::GATEWAY_TIMEOUT,
            Json(serde_json::json!({ "ok": false, "message": "timed out waiting for the ECU" })),
        )
            .into_response(),
    }
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
