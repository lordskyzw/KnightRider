//! Core application logic.

pub mod datastore;
pub mod signals;
pub mod state_machine;

pub use datastore::DataStore;
pub use signals::Signal;
pub use state_machine::{State, StateMachine};
