//! `/backlog?since=N&limit=M` — courier's view of Pi-built batches.
//!
//! Returns NDJSON (newline-delimited JSON), one row per batch:
//!
//! ```json
//! {"batch_id": 42, "created_at": "...", "sample_count": 17,
//!  "envelope_b64": "..."}
//! ```
//!
//! The `envelope_b64` field is the base64-encoded CBOR-encoded
//! [`BatchEnvelope`] — the courier stores it verbatim and forwards it to the
//! cloud unmodified, so the Pi's (future) signature stays intact end-to-end.

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine;
use serde::{Deserialize, Serialize};

use crate::server::AppState;

#[derive(Debug, Deserialize)]
pub struct BacklogQuery {
    #[serde(default)]
    pub since: u64,
    pub limit: Option<usize>,
}

#[derive(Debug, Serialize)]
struct BacklogRow {
    batch_id: u64,
    created_at: String,
    sample_count: u32,
    envelope_b64: String,
}

const DEFAULT_LIMIT: usize = 100;
const MAX_LIMIT: usize = 1000;

pub async fn handler(State(state): State<AppState>, Query(q): Query<BacklogQuery>) -> Response {
    let limit = q.limit.unwrap_or(DEFAULT_LIMIT).min(MAX_LIMIT);
    let store = state.store.clone();

    let result = tokio::task::spawn_blocking(move || {
        let guard = store.lock().expect("store mutex poisoned");
        guard.query_since(q.since, limit)
    })
    .await;

    let envelopes = match result {
        Ok(Ok(v)) => v,
        Ok(Err(e)) => {
            log::warn!("backlog: store error: {}", e);
            return (StatusCode::INTERNAL_SERVER_ERROR, "store error").into_response();
        }
        Err(e) => {
            log::warn!("backlog: blocking task panicked: {}", e);
            return (StatusCode::INTERNAL_SERVER_ERROR, "task panicked").into_response();
        }
    };

    let mut body = String::with_capacity(envelopes.len() * 256);
    for env in envelopes {
        let encoded = match env.encode() {
            Ok(b) => b,
            Err(e) => {
                log::warn!("backlog: envelope re-encode failed for batch {}: {}", env.batch_id, e);
                continue;
            }
        };
        let row = BacklogRow {
            batch_id: env.batch_id,
            created_at: env.created_at.to_rfc3339(),
            sample_count: env.sample_count,
            envelope_b64: B64.encode(&encoded),
        };
        match serde_json::to_string(&row) {
            Ok(line) => {
                body.push_str(&line);
                body.push('\n');
            }
            Err(e) => log::warn!("backlog: row serialize failed: {}", e),
        }
    }

    (
        StatusCode::OK,
        [("content-type", "application/x-ndjson")],
        body,
    )
        .into_response()
}
