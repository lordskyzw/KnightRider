//! `/ws/live` — WebSocket broadcast of decoded samples.
//!
//! Each client gets its own subscription to the extractor broadcast channel.
//! Slow clients fall behind via `RecvError::Lagged` and skip samples rather
//! than blocking the broadcast — the canonical buffer is the durable store
//! for those clients to reconcile via `/backlog`.

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::response::Response;
use tokio::sync::broadcast::error::RecvError;

use crate::server::AppState;

pub async fn ws_handler(ws: WebSocketUpgrade, State(state): State<AppState>) -> Response {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(mut socket: WebSocket, state: AppState) {
    let mut rx = state.samples_tx.subscribe();
    log::debug!("ws/live: client connected");

    loop {
        tokio::select! {
            sample = rx.recv() => {
                match sample {
                    Ok(sample) => {
                        let json = match serde_json::to_string(&sample) {
                            Ok(s) => s,
                            Err(e) => {
                                log::warn!("ws/live: sample serialize failed: {}", e);
                                continue;
                            }
                        };
                        if socket.send(Message::Text(json)).await.is_err() {
                            break;
                        }
                    }
                    Err(RecvError::Lagged(n)) => {
                        log::warn!("ws/live: client lagging, skipped {} samples", n);
                    }
                    Err(RecvError::Closed) => break,
                }
            }
            incoming = socket.recv() => {
                match incoming {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(_)) => break,
                    _ => {}
                }
            }
        }
    }

    log::debug!("ws/live: client disconnected");
}
