//! Canonical telemetry buffer on the Pi.
//!
//! The Pi is the canonical store. Phones are couriers that pull
//! `/backlog?since=N` and carry batches to the cloud. The buffer is responsible
//! for:
//!
//! - Building versioned [`schema::BatchEnvelope`]s from streamed samples.
//! - Persisting them in SQLite (WAL) with a monotonic `batch_id`.
//! - Serving range queries for the backlog endpoint.

pub mod schema;
pub mod store;
pub mod writer;
