//! In-memory data storage.
//!
//! Stores the latest signal values for access by other components.

use std::collections::HashMap;
use super::signals::{Signal, SignalKind};

/// Thread-safe in-memory storage for latest signal values.
#[derive(Debug, Default)]
pub struct DataStore {
    signals: HashMap<String, Signal>,
}

impl DataStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Stores a signal value, replacing any previous value.
    pub fn store(&mut self, signal: Signal) {
        self.signals.insert(signal.kind.name().to_string(), signal);
    }

    /// Gets the latest value for a signal kind.
    pub fn get(&self, kind: SignalKind) -> Option<&Signal> {
        self.signals.get(kind.name())
    }

    /// Returns all stored signals.
    pub fn all(&self) -> impl Iterator<Item = &Signal> {
        self.signals.values()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_datastore() {
        let mut store = DataStore::new();
        let signal = Signal::new(SignalKind::EngineRpm, 3000.0, "rpm");
        store.store(signal);
        
        let retrieved = store.get(SignalKind::EngineRpm).unwrap();
        assert_eq!(retrieved.value, 3000.0);
    }
}
