//! `/inbox` — cloud→Pi delivery point.
//!
//! Couriers POST bundles here (discovery-track results, known-track model
//! updates, config). Stubbed for the demo phase: accepts any JSON body and
//! logs it. The real handler will dispatch by `bundle_kind`, verify cloud
//! signature, stage the bundle, and atomically swap it in.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::Value;

pub async fn handler(Json(body): Json<Value>) -> Response {
    log::info!(
        "inbox: received payload (kind={:?}, len~{} bytes)",
        body.get("bundle_kind").and_then(Value::as_str),
        body.to_string().len()
    );
    (StatusCode::ACCEPTED, Json(serde_json::json!({ "received": true }))).into_response()
}
