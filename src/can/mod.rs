//! CAN bus interface layer.
//!
//! This module provides the low-level interface to the CAN bus via SocketCAN,
//! as well as higher-level protocol handling for ISO-TP and OBD-II.

pub mod interface;
pub mod isotp;
pub mod obd;
pub mod scheduler;

pub use interface::{CanFrame, CanInterface};
pub use isotp::{IsoTpError, IsoTpSession};
pub use obd::{ObdError, ObdPid, ObdRequest, ObdResponse, ObdService};
pub use scheduler::RequestScheduler;
