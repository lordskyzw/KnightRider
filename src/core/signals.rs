//! Decoded sensor values.
//!
//! Represents the decoded values from OBD-II responses with timestamps.

use chrono::{DateTime, Utc};

/// A decoded sensor signal with timestamp.
#[derive(Debug, Clone)]
pub struct Signal {
    /// Timestamp when the value was received.
    pub timestamp: DateTime<Utc>,
    /// Signal type.
    pub kind: SignalKind,
    /// Decoded value.
    pub value: f64,
    /// Unit string.
    pub unit: &'static str,
}

impl Signal {
    pub fn new(kind: SignalKind, value: f64, unit: &'static str) -> Self {
        Self {
            timestamp: Utc::now(),
            kind,
            value,
            unit,
        }
    }

    /// Formats the signal for console output.
    pub fn format_console(&self) -> String {
        let ts = self.timestamp.format("%Y-%m-%dT%H:%M:%S%.3fZ");
        match self.kind {
            SignalKind::Timeout(name) => format!("[{}] {}: TIMEOUT", ts, name),
            _ => format!("[{}] {}: {:.0} {}", ts, self.kind.name(), self.value, self.unit),
        }
    }
}

/// Types of signals we can decode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalKind {
    EngineRpm,
    VehicleSpeed,
    CoolantTemperature,
    IntakeAirTemperature,
    ThrottlePosition,
    FuelTankLevel,
    Timeout(&'static str),
}

impl SignalKind {
    pub fn name(&self) -> &'static str {
        match self {
            SignalKind::EngineRpm => "RPM",
            SignalKind::VehicleSpeed => "Speed",
            SignalKind::CoolantTemperature => "Coolant Temp",
            SignalKind::IntakeAirTemperature => "Intake Air Temp",
            SignalKind::ThrottlePosition => "Throttle",
            SignalKind::FuelTankLevel => "Fuel Level",
            SignalKind::Timeout(name) => name,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_signal_format() {
        let signal = Signal::new(SignalKind::EngineRpm, 3000.0, "rpm");
        let output = signal.format_console();
        assert!(output.contains("RPM: 3000 rpm"));
    }
}
