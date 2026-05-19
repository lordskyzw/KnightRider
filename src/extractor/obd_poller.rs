//! OBD-II request/response extractor.
//!
//! Owns a [`CanInterface`] on a dedicated blocking thread, polls a configured
//! PID list in round-robin, decodes each response, and publishes [`Sample`]s
//! into a tokio broadcast channel.
//!
//! The thread stays alive even when there are zero subscribers — that way the
//! local server and future viewers can attach without restarting the poller.
//! (Samples emitted with no subscribers are simply dropped by `broadcast`.)
//!
//! Real CAN I/O is Linux-only via SocketCAN; on other platforms
//! [`CanInterface`] returns `NotSupported` and the loop logs warnings.

use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::can::interface::CanError;
use crate::can::obd::addressing;
use crate::can::{CanFrame, CanInterface, IsoTpSession, ObdPid, ObdRequest, ObdResponse};
use crate::extractor::sample::{Sample, SampleSource};
use crate::extractor::SampleSender;

/// PID set polled by default if the caller does not override.
pub const DEFAULT_PIDS: &[ObdPid] = &[
    ObdPid::EngineRpm,
    ObdPid::VehicleSpeed,
    ObdPid::CoolantTemperature,
    ObdPid::IntakeAirTemperature,
    ObdPid::ThrottlePosition,
    ObdPid::FuelTankLevel,
];

#[derive(Debug, Clone)]
pub struct ObdPollerConfig {
    pub pids: Vec<ObdPid>,
    pub poll_interval: Duration,
    pub response_timeout: Duration,
}

impl Default for ObdPollerConfig {
    fn default() -> Self {
        Self {
            pids: DEFAULT_PIDS.to_vec(),
            poll_interval: Duration::from_millis(100),
            response_timeout: Duration::from_millis(200),
        }
    }
}

/// Spawns the OBD poller on a dedicated thread.
///
/// `can` is moved into the thread. The thread runs until the process exits.
pub fn spawn(can: CanInterface, tx: SampleSender, config: ObdPollerConfig) -> JoinHandle<()> {
    std::thread::Builder::new()
        .name("obd-poller".into())
        .spawn(move || run(can, tx, config))
        .expect("spawn obd-poller thread")
}

fn run(can: CanInterface, tx: SampleSender, config: ObdPollerConfig) {
    if config.pids.is_empty() {
        log::warn!("obd-poller: no PIDs configured, exiting");
        return;
    }

    let mut isotp = IsoTpSession::new();
    let mut pid_idx: usize = 0;

    log::info!("obd-poller: starting, {} PIDs, poll_interval={:?}", config.pids.len(), config.poll_interval);

    loop {
        let pid = config.pids[pid_idx % config.pids.len()];
        pid_idx = pid_idx.wrapping_add(1);

        if let Err(e) = send_request(&can, pid) {
            log::warn!("obd-poller: send failed for {:?}: {}", pid, e);
            std::thread::sleep(config.poll_interval);
            continue;
        }
        isotp.reset();

        if let Some((ecu_id, payload)) = await_response(&can, &mut isotp, config.response_timeout) {
            match ObdResponse::parse(ecu_id, &payload).and_then(|r| r.decode(pid)) {
                Ok(decoded) => {
                    let sample = Sample::new(
                        SampleSource::ObdPoller,
                        signal_name(pid),
                        decoded.value,
                        pid.unit(),
                    );
                    // Send failure means there are no live receivers; that's fine.
                    let _ = tx.send(sample);
                }
                Err(e) => log::debug!("obd-poller: decode failed for {:?}: {}", pid, e),
            }
        }

        std::thread::sleep(config.poll_interval);
    }
}

fn send_request(can: &CanInterface, pid: ObdPid) -> Result<(), CanError> {
    let request = ObdRequest::current_data(pid);
    let frame = CanFrame::new(request.can_id(), &request.to_can_data());
    can.send(&frame)
}

fn await_response(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    timeout: Duration,
) -> Option<(u32, Vec<u8>)> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) if addressing::is_obd_response(frame.id) => match isotp.receive(frame.data()) {
                Ok(Some(payload)) => return Some((frame.id, payload)),
                Ok(None) => continue,
                Err(_) => return None,
            },
            Ok(_) => continue,
            Err(CanError::Timeout) => continue,
            Err(_) => return None,
        }
    }
    None
}

/// Canonical wire name for an OBD PID. Used as [`Sample::signal`].
pub fn signal_name(pid: ObdPid) -> &'static str {
    match pid {
        ObdPid::EngineRpm => "obd.rpm",
        ObdPid::VehicleSpeed => "obd.speed",
        ObdPid::CoolantTemperature => "obd.coolant_temp",
        ObdPid::IntakeAirTemperature => "obd.intake_air_temp",
        ObdPid::ThrottlePosition => "obd.throttle",
        ObdPid::FuelTankLevel => "obd.fuel_level",
        ObdPid::SupportedPids01To20 => "obd.supported_01_20",
        ObdPid::SupportedPids21To40 => "obd.supported_21_40",
    }
}
