//! Sample type emitted by extractors into the broadcast channel.
//!
//! Signals are addressed by canonical dot-notation name so that OBD-II PIDs
//! and DBC-decoded sniffer signals share one type without sharing an enum.
//!
//! - OBD-II:  `"obd.rpm"`, `"obd.speed"`, `"obd.coolant_temp"`, ...
//! - Sniffer: `"dbc.<profile>.<message>.<signal>"`

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Origin of a [`Sample`] — useful for downstream filtering and for marking
/// data provenance in the canonical buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SampleSource {
    /// Came from an OBD-II request/response cycle.
    ObdPoller,
    /// Came from passive CAN sniffing decoded via DBC.
    Sniffer,
}

/// A single decoded signal observation flowing through the pipeline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Sample {
    pub ts: DateTime<Utc>,
    pub source: SampleSource,
    pub signal: String,
    pub value: f64,
    pub unit: String,
}

impl Sample {
    pub fn new(source: SampleSource, signal: impl Into<String>, value: f64, unit: impl Into<String>) -> Self {
        Self {
            ts: Utc::now(),
            source,
            signal: signal.into(),
            value,
            unit: unit.into(),
        }
    }
}
