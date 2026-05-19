//! `/known-track/alerts` — on-Pi known-track inference results.
//!
//! Stub for the demo phase. Returns an empty array. The real handler will
//! pull recent alerts from the known-track inference engine (DTC + rule + a
//! small model) once that ships.

use axum::response::Json;
use serde_json::{json, Value};

pub async fn alerts_handler() -> Json<Value> {
    Json(json!({ "alerts": [] }))
}
