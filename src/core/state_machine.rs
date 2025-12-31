//! Application state machine.
//!
//! Manages the lifecycle of Knight Rider: IDLE → INIT → CONNECTED → RUNNING → ERROR.

use std::fmt;

/// Application states.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// Just started, not yet initialized.
    Idle,
    /// Initializing CAN interface.
    Initializing,
    /// CAN interface ready, attempting to communicate with ECUs.
    Connected,
    /// Successfully communicating with ECUs, polling data.
    Running,
    /// Error state - attempting recovery.
    Error,
}

impl fmt::Display for State {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            State::Idle => write!(f, "IDLE"),
            State::Initializing => write!(f, "INITIALIZING"),
            State::Connected => write!(f, "CONNECTED"),
            State::Running => write!(f, "RUNNING"),
            State::Error => write!(f, "ERROR"),
        }
    }
}

/// State machine for application lifecycle.
#[derive(Debug)]
pub struct StateMachine {
    state: State,
    error_count: u32,
    last_valid_frame_count: u32,
}

impl Default for StateMachine {
    fn default() -> Self {
        Self::new()
    }
}

impl StateMachine {
    pub fn new() -> Self {
        Self {
            state: State::Idle,
            error_count: 0,
            last_valid_frame_count: 0,
        }
    }

    pub fn state(&self) -> State {
        self.state
    }

    pub fn transition_to(&mut self, new_state: State) {
        log::info!("State transition: {} → {}", self.state, new_state);
        self.state = new_state;
        if new_state != State::Error {
            self.error_count = 0;
        }
    }

    pub fn record_error(&mut self) {
        self.error_count += 1;
        log::warn!("Error count: {}", self.error_count);
        if self.error_count >= 5 {
            self.transition_to(State::Error);
        }
    }

    pub fn record_success(&mut self) {
        self.last_valid_frame_count += 1;
        if self.state == State::Connected && self.last_valid_frame_count >= 1 {
            self.transition_to(State::Running);
        }
    }

    pub fn reset(&mut self) {
        self.error_count = 0;
        self.last_valid_frame_count = 0;
        self.transition_to(State::Idle);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_state_transitions() {
        let mut sm = StateMachine::new();
        assert_eq!(sm.state(), State::Idle);
        
        sm.transition_to(State::Initializing);
        assert_eq!(sm.state(), State::Initializing);
        
        sm.transition_to(State::Connected);
        sm.record_success();
        assert_eq!(sm.state(), State::Running);
    }
}
