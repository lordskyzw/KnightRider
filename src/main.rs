//! Knight Rider - ECU Diagnostic Computer
//!
//! A field-grade automotive diagnostic tool for Raspberry Pi.
//! Communicates with vehicle ECUs via CAN / OBD-II.
//!
//! # Usage
//!
//! ```bash
//! # Run with real CAN interface
//! ./knight-rider --interface can0
//!
//! # Run with virtual CAN (for testing)
//! ./knight-rider --interface vcan0
//!
//! # Enable debug logging
//! RUST_LOG=debug ./knight-rider --interface vcan0
//! ```

mod can;
mod core;
mod logging;
mod ui;

use std::path::PathBuf;
use std::time::{Duration, Instant};

use chrono::Utc;

use crate::can::{CanFrame, CanInterface, IsoTpSession, ObdPid, ObdRequest, ObdResponse};
use crate::can::obd::addressing;
use crate::can::scheduler::RequestScheduler;
use crate::core::signals::{Signal, SignalKind};
use crate::core::state_machine::{State, StateMachine};
use crate::logging::timeseries::{RawFrameEntry, TimeseriesLogger};

/// Configuration for the application.
struct Config {
    interface_name: String,
    run_duration: Duration,
    log_path: PathBuf,
}

impl Config {
    fn from_args() -> Self {
        let args: Vec<String> = std::env::args().collect();
        
        let interface_name = args
            .iter()
            .position(|a| a == "--interface" || a == "-i")
            .and_then(|i| args.get(i + 1))
            .map(|s| s.clone())
            .unwrap_or_else(|| "vcan0".to_string());

        let run_duration = args
            .iter()
            .position(|a| a == "--duration" || a == "-d")
            .and_then(|i| args.get(i + 1))
            .and_then(|s| s.parse().ok())
            .map(Duration::from_secs)
            .unwrap_or(Duration::from_secs(60));

        let log_path = PathBuf::from("/tmp/knight-rider-raw.log");

        Self {
            interface_name,
            run_duration,
            log_path,
        }
    }
}

/// Main application entry point.
fn main() {
    // Initialize logging
    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info")
    ).init();

    log::info!("Knight Rider v{}", env!("CARGO_PKG_VERSION"));
    log::info!("Field-grade ECU diagnostic computer");

    let config = Config::from_args();
    
    if let Err(e) = run(config) {
        log::error!("Application error: {}", e);
        std::process::exit(1);
    }
}

/// Runs the main application loop.
fn run(config: Config) -> Result<(), Box<dyn std::error::Error>> {
    log::info!("Opening CAN interface: {}", config.interface_name);

    // Initialize CAN interface
    let mut can = CanInterface::open(&config.interface_name)?;
    can.set_read_timeout(Some(Duration::from_millis(200)))?;

    log::info!("CAN interface opened successfully");

    // Initialize logger
    let mut logger = TimeseriesLogger::new(config.log_path.clone())?;
    log::info!("Logging raw frames to: {}", config.log_path.display());

    // Initialize state machine and scheduler
    let mut state_machine = StateMachine::new();
    let mut scheduler = RequestScheduler::default();
    let mut isotp_session = IsoTpSession::new();

    state_machine.transition_to(State::Initializing);

    // Query supported PIDs first
    log::info!("Querying supported PIDs...");
    query_supported_pids(&can, &mut isotp_session)?;

    state_machine.transition_to(State::Connected);

    // Main polling loop
    let start_time = Instant::now();
    let request = ObdRequest::current_data(ObdPid::EngineRpm);

    log::info!("Starting RPM polling for {} seconds", config.run_duration.as_secs());

    while start_time.elapsed() < config.run_duration {
        // Wait for next polling interval
        scheduler.wait_for_next();

        // Send RPM request
        let request_frame = CanFrame::new(request.can_id(), &request.to_can_data());
        
        if let Err(e) = can.send(&request_frame) {
            log::warn!("Failed to send request: {}", e);
            state_machine.record_error();
            continue;
        }

        scheduler.mark_sent();
        isotp_session.reset();

        // Wait for response
        let response = wait_for_response(&can, &mut isotp_session, &mut logger, scheduler.timeout());

        match response {
            Some(payload) => {
                match ObdResponse::parse(0x7E8, &payload) {
                    Ok(response) => {
                        if let Ok(decoded) = response.decode(ObdPid::EngineRpm) {
                            let signal = Signal::new(
                                SignalKind::EngineRpm,
                                decoded.value,
                                decoded.unit,
                            );
                            println!("{}", signal.format_console());
                            state_machine.record_success();
                        }
                    }
                    Err(e) => {
                        log::warn!("Failed to parse response: {}", e);
                        state_machine.record_error();
                    }
                }
            }
            None => {
                // Timeout
                let signal = Signal::new(SignalKind::Timeout("RPM"), 0.0, "");
                println!("{}", signal.format_console());
            }
        }

        // Check state
        if state_machine.state() == State::Error {
            log::warn!("Too many errors, attempting recovery...");
            std::thread::sleep(Duration::from_secs(1));
            state_machine.transition_to(State::Connected);
        }
    }

    log::info!("Polling complete. Total duration: {:?}", start_time.elapsed());
    logger.flush()?;

    Ok(())
}

/// Waits for an OBD-II response within the timeout period.
fn wait_for_response(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    logger: &mut TimeseriesLogger,
    timeout: Duration,
) -> Option<Vec<u8>> {
    let start = Instant::now();

    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) => {
                // Log raw frame
                let entry = RawFrameEntry {
                    timestamp: Utc::now(),
                    can_id: frame.id,
                    dlc: frame.dlc,
                    data: frame.data,
                };
                let _ = logger.log_frame(&entry);

                // Check if this is an OBD-II response
                if !addressing::is_obd_response(frame.id) {
                    continue;
                }

                // Process through ISO-TP
                match isotp.receive(frame.data()) {
                    Ok(Some(payload)) => return Some(payload),
                    Ok(None) => continue, // Need more frames
                    Err(e) => {
                        log::warn!("ISO-TP error: {}", e);
                        return None;
                    }
                }
            }
            Err(crate::can::interface::CanError::Timeout) => {
                // Continue waiting
            }
            Err(e) => {
                log::warn!("CAN receive error: {}", e);
                return None;
            }
        }
    }

    None
}

/// Queries supported PIDs (Mode 01 PID 00).
fn query_supported_pids(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
) -> Result<(), Box<dyn std::error::Error>> {
    let request = ObdRequest::current_data(ObdPid::SupportedPids01To20);
    let frame = CanFrame::new(request.can_id(), &request.to_can_data());

    can.send(&frame)?;

    // Wait briefly for response (we don't strictly need it for v1)
    let timeout = Duration::from_millis(500);
    let start = Instant::now();

    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) if addressing::is_obd_response(frame.id) => {
                if let Ok(Some(payload)) = isotp.receive(frame.data()) {
                    if let Ok(response) = ObdResponse::parse(frame.id, &payload) {
                        let supported = crate::can::obd::parse_supported_pids(&response.data);
                        log::info!("ECU 0x{:03X} supports PIDs: {:?}", frame.id, supported);
                        return Ok(());
                    }
                }
            }
            Ok(_) => continue,
            Err(crate::can::interface::CanError::Timeout) => continue,
            Err(_) => break,
        }
    }

    log::warn!("No response to supported PIDs query (ECU may be offline)");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_defaults() {
        // Just verify it doesn't panic
        let _ = Config {
            interface_name: "vcan0".to_string(),
            run_duration: Duration::from_secs(60),
            log_path: PathBuf::from("/tmp/test.log"),
        };
    }
}
