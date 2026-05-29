//! OBD-II request/response extractor.
//!
//! Owns a [`CanInterface`] on a dedicated blocking thread and runs three
//! cadences:
//!
//! 1. **Fast PID loop** — round-robins through [`ObdPollerConfig::pids`] at
//!    `poll_interval` (default 100 ms per PID). Each successful response is
//!    decoded and emitted as a [`Sample`].
//! 2. **Slow DTC sweep** — every `dtc_interval` (default 30 s) sends a Mode
//!    0x03 request, parses the response into a list of stored DTCs, and
//!    emits one sample per code (signal name like `dtc.stored.p0301`,
//!    value `1.0`). When the list shrinks (a code clears), a corresponding
//!    `dtc.cleared.*` sample is emitted with value `1.0`.
//! 3. **One-shot Mode 09** — at startup, queries the VIN, calibration ID,
//!    and ECU name. Emitted as `session.vin` / `session.cal_id` /
//!    `session.ecu_name` "string-valued" samples (`value = 1.0`, the
//!    string is squeezed into `unit`).
//!
//! Multi-frame ISO-TP responses (VIN, multi-DTC lists) are reassembled
//! with manual flow-control: when we see a First Frame we transmit a
//! `30 00 00` FC frame to the ECU's request address (typically `0x7E0`).
//!
//! Real CAN I/O is Linux-only via SocketCAN; on other platforms
//! [`CanInterface`] returns `NotSupported` and the loop logs warnings.

use std::collections::HashSet;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::can::dtc::DtcCode;
use crate::can::interface::CanError;
use crate::can::obd::{addressing, build_stored_dtc_request, build_vehicle_info_request, ObdService};
use crate::can::{CanFrame, CanInterface, IsoTpSession, ObdPid, ObdRequest, ObdResponse};
use crate::extractor::sample::{Sample, SampleSource};
use crate::extractor::SampleSender;

/// PID set polled by default if the caller does not override.
///
/// Superset verified across the Toyota Vitz DBA-NSP130 (Mode 01 PID 00 bitmap
/// `BE 3F A8 13`, 2026-05-28) and Corolla Axio E160 (2026-05-29). The Axio scan
/// added battery/charging voltage, catalyst temps (relevant to its stored
/// P0420), barometric pressure, and commanded lambda. A PID a given ECU doesn't
/// support just yields no response and the loop moves on.
pub const DEFAULT_PIDS: &[ObdPid] = &[
    ObdPid::EngineRpm,
    ObdPid::VehicleSpeed,
    ObdPid::EngineLoad,
    ObdPid::CoolantTemperature,
    ObdPid::IntakeAirTemperature,
    ObdPid::IntakeManifoldPressure,
    ObdPid::MafAirFlowRate,
    ObdPid::ThrottlePosition,
    ObdPid::TimingAdvance,
    ObdPid::ShortTermFuelTrimBank1,
    ObdPid::LongTermFuelTrimBank1,
    ObdPid::O2SensorBank1Sensor2,
    ObdPid::RunTimeSinceStart,
    ObdPid::FuelTankLevel,
    ObdPid::BatteryVoltage,
    ObdPid::AbsoluteLoadValue,
    ObdPid::BarometricPressure,
    ObdPid::CatalystTempBank1Sensor1,
    ObdPid::CatalystTempBank1Sensor2,
    ObdPid::CommandedEquivalenceRatio,
];

#[derive(Debug, Clone)]
pub struct ObdPollerConfig {
    pub pids: Vec<ObdPid>,
    pub poll_interval: Duration,
    pub response_timeout: Duration,
    pub dtc_interval: Duration,
    /// Read VIN / calibration ID / ECU name once at startup.
    pub read_vehicle_info_at_start: bool,
}

impl Default for ObdPollerConfig {
    fn default() -> Self {
        Self {
            pids: DEFAULT_PIDS.to_vec(),
            poll_interval: Duration::from_millis(100),
            response_timeout: Duration::from_millis(250),
            dtc_interval: Duration::from_secs(30),
            read_vehicle_info_at_start: true,
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

    log::info!(
        "obd-poller: starting · {} PIDs · poll_interval={:?} · dtc_interval={:?}",
        config.pids.len(),
        config.poll_interval,
        config.dtc_interval
    );

    // One-shot vehicle info read at startup.
    if config.read_vehicle_info_at_start {
        let mut isotp = IsoTpSession::new();
        // Try a few common Mode 09 info PIDs. Failures are non-fatal — many
        // ECUs implement only a subset.
        for (info_pid, signal) in [
            (0x02u8, "session.vin"),
            (0x04u8, "session.cal_id"),
            (0x0Au8, "session.ecu_name"),
        ] {
            match read_vehicle_info(&can, &mut isotp, info_pid, config.response_timeout) {
                Ok(text) => {
                    log::info!("obd-poller: {} = {:?}", signal, text);
                    let _ = tx.send(Sample::new(
                        SampleSource::ObdPoller,
                        signal,
                        1.0,
                        text,
                    ));
                }
                Err(why) => log::debug!(
                    "obd-poller: Mode 09 PID 0x{:02X} unavailable ({})",
                    info_pid,
                    why
                ),
            }
        }
    }

    let mut isotp = IsoTpSession::new();
    let mut pid_idx: usize = 0;
    let mut last_dtc = Instant::now() - config.dtc_interval; // poll once up-front
    let mut known_stored: HashSet<DtcCode> = HashSet::new();

    loop {
        // Slow DTC sweep, opportunistic.
        if last_dtc.elapsed() >= config.dtc_interval {
            isotp.reset();
            match read_stored_dtcs(&can, &mut isotp, config.response_timeout) {
                Ok(list) => {
                    let current: HashSet<DtcCode> = list.iter().copied().collect();
                    // Newly-stored codes.
                    for code in &current {
                        let _ = tx.send(Sample::new(
                            SampleSource::ObdPoller,
                            format!("dtc.stored.{}", code.as_signal()),
                            1.0,
                            "",
                        ));
                    }
                    // Cleared codes (in last sweep, not in this one).
                    for code in known_stored.difference(&current) {
                        let _ = tx.send(Sample::new(
                            SampleSource::ObdPoller,
                            format!("dtc.cleared.{}", code.as_signal()),
                            1.0,
                            "",
                        ));
                    }
                    if current != known_stored {
                        log::info!(
                            "obd-poller: stored DTC set = {:?}",
                            current.iter().map(|d| d.to_string()).collect::<Vec<_>>()
                        );
                    }
                    known_stored = current;
                    // Emit a heartbeat count so even "no codes" produces a row.
                    let _ = tx.send(Sample::new(
                        SampleSource::ObdPoller,
                        "obd.dtc_count",
                        known_stored.len() as f64,
                        "",
                    ));
                }
                Err(why) => log::debug!("obd-poller: DTC sweep failed ({})", why),
            }
            last_dtc = Instant::now();
        }

        // One fast-loop PID.
        let pid = config.pids[pid_idx % config.pids.len()];
        pid_idx = pid_idx.wrapping_add(1);

        if let Err(e) = send_request(&can, pid) {
            log::warn!("obd-poller: send failed for {:?}: {}", pid, e);
            std::thread::sleep(config.poll_interval);
            continue;
        }
        isotp.reset();

        if let Some((ecu_id, payload)) =
            await_response(&can, &mut isotp, config.response_timeout)
        {
            match ObdResponse::parse(ecu_id, &payload).and_then(|r| r.decode(pid)) {
                Ok(decoded) => {
                    let _ = tx.send(Sample::new(
                        SampleSource::ObdPoller,
                        signal_name(pid),
                        decoded.value,
                        pid.unit(),
                    ));
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

/// Wait up to `timeout` for an OBD-II response on any 0x7E8-0x7EF ID.
///
/// Drives the ISO-TP session, sending a Flow Control frame back to the ECU
/// (one ID below the response ID — e.g. 0x7E0 for an 0x7E8 responder) when
/// a First Frame arrives, so multi-frame replies (Mode 09 VIN, large DTC
/// lists) reassemble correctly.
fn await_response(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    timeout: Duration,
) -> Option<(u32, Vec<u8>)> {
    let start = Instant::now();

    while start.elapsed() < timeout {
        match can.recv() {
            Ok(frame) if addressing::is_obd_response(frame.id) => {
                let was_receiving = isotp.is_receiving();
                match isotp.receive(frame.data()) {
                    Ok(Some(payload)) => return Some((frame.id, payload)),
                    Ok(None) => {
                        // First frame just arrived — send FC to keep the
                        // conversation going. ECU's RX address is one octet
                        // below its TX address (e.g. 0x7E8 responds → we
                        // send FC to 0x7E0).
                        if !was_receiving && isotp.is_receiving() {
                            let fc = IsoTpSession::build_flow_control(0, 0);
                            let fc_id = frame.id.saturating_sub(8);
                            let _ = can.send(&CanFrame::new(fc_id, &fc));
                        }
                        continue;
                    }
                    Err(_) => return None,
                }
            }
            Ok(_) => continue,
            Err(CanError::Timeout) => continue,
            Err(_) => return None,
        }
    }
    None
}

/// Send Mode 0x03 (read stored DTCs) and return the parsed code list.
fn read_stored_dtcs(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    timeout: Duration,
) -> Result<Vec<DtcCode>, &'static str> {
    let (data, id) = build_stored_dtc_request();
    can.send(&CanFrame::new(id, &data))
        .map_err(|_| "send failed")?;
    isotp.reset();

    let (_ecu, payload) = await_response(can, isotp, timeout).ok_or("no response")?;
    // payload[0] should be the response mode (0x43); strip it.
    if payload.is_empty() || payload[0] != ObdService::StoredDtcs.response_mode() {
        return Err("unexpected mode in response");
    }
    Ok(crate::can::dtc::parse_dtc_list(&payload[1..]))
}

/// Send Mode 0x09 with the given info-PID and return the decoded ASCII string.
fn read_vehicle_info(
    can: &CanInterface,
    isotp: &mut IsoTpSession,
    info_pid: u8,
    timeout: Duration,
) -> Result<String, &'static str> {
    let (data, id) = build_vehicle_info_request(info_pid);
    can.send(&CanFrame::new(id, &data))
        .map_err(|_| "send failed")?;
    isotp.reset();

    let (_ecu, payload) = await_response(can, isotp, timeout).ok_or("no response")?;
    // Layout: [49 InfoPID NumDataItems  D D D ... ]
    if payload.len() < 3 || payload[0] != ObdService::VehicleInfo.response_mode() {
        return Err("unexpected mode in response");
    }
    if payload[1] != info_pid {
        return Err("info-PID mismatch");
    }
    // payload[2] is the count of data items; the rest is the ASCII string,
    // possibly padded with 0x00 to align.
    let text: String = payload[3..]
        .iter()
        .copied()
        .filter(|b| (0x20..=0x7E).contains(b))
        .map(|b| b as char)
        .collect();
    if text.is_empty() {
        return Err("empty payload");
    }
    Ok(text.trim().to_string())
}

/// Canonical wire name for an OBD PID. Used as [`Sample::signal`].
pub fn signal_name(pid: ObdPid) -> &'static str {
    match pid {
        ObdPid::EngineRpm => "obd.rpm",
        ObdPid::VehicleSpeed => "obd.speed",
        ObdPid::EngineLoad => "obd.engine_load",
        ObdPid::CoolantTemperature => "obd.coolant_temp",
        ObdPid::ShortTermFuelTrimBank1 => "obd.stft_b1",
        ObdPid::LongTermFuelTrimBank1 => "obd.ltft_b1",
        ObdPid::IntakeManifoldPressure => "obd.map",
        ObdPid::IntakeAirTemperature => "obd.intake_air_temp",
        ObdPid::TimingAdvance => "obd.timing_advance",
        ObdPid::MafAirFlowRate => "obd.maf",
        ObdPid::ThrottlePosition => "obd.throttle",
        ObdPid::FuelSystemStatus => "obd.fuel_system_status",
        ObdPid::O2SensorsPresent => "obd.o2_sensors_present",
        ObdPid::O2SensorBank1Sensor2 => "obd.o2_b1s2_v",
        ObdPid::RunTimeSinceStart => "obd.run_time_s",
        ObdPid::FuelTankLevel => "obd.fuel_level",
        ObdPid::BarometricPressure => "obd.baro_pressure",
        ObdPid::CatalystTempBank1Sensor1 => "obd.cat_temp_b1s1",
        ObdPid::CatalystTempBank1Sensor2 => "obd.cat_temp_b1s2",
        ObdPid::BatteryVoltage => "obd.battery_v",
        ObdPid::AbsoluteLoadValue => "obd.abs_load",
        ObdPid::CommandedEquivalenceRatio => "obd.commanded_lambda",
        ObdPid::AmbientAirTemperature => "obd.ambient_air_temp",
        ObdPid::OilTemperature => "obd.oil_temp",
        ObdPid::EngineFuelRate => "obd.fuel_rate",
        ObdPid::SupportedPids01To20 => "obd.supported_01_20",
        ObdPid::SupportedPids21To40 => "obd.supported_21_40",
    }
}
