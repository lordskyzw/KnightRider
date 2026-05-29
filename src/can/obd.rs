//! OBD-II protocol implementation.
//!
//! Implements ISO 15031 / SAE J1979 over CAN bus. Modes 0x01 (current data)
//! and 0x09 (vehicle info) supported on the request side; Mode 0x03 (stored
//! DTCs) is handled by [`super::dtc`].

use std::fmt;
use super::isotp::IsoTpSession;

/// OBD-II CAN addressing constants.
pub mod addressing {
    pub const OBD_REQUEST_ID: u32 = 0x7DF;
    pub const OBD_RESPONSE_ID_START: u32 = 0x7E8;
    pub const OBD_RESPONSE_ID_END: u32 = 0x7EF;

    pub fn is_obd_response(can_id: u32) -> bool {
        can_id >= OBD_RESPONSE_ID_START && can_id <= OBD_RESPONSE_ID_END
    }
}

/// OBD-II service (mode) identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObdService {
    /// Mode 0x01 — show current data.
    CurrentData = 0x01,
    /// Mode 0x03 — show stored DTCs (read-only; reply parsed by `dtc.rs`).
    StoredDtcs = 0x03,
    /// Mode 0x09 — request vehicle information (VIN, calibration ID, ECU name).
    VehicleInfo = 0x09,
}

impl ObdService {
    pub fn response_mode(self) -> u8 {
        (self as u8) + 0x40
    }
}

/// OBD-II Parameter ID (PID) definitions for Mode 0x01 current data.
///
/// Verified-supported across the cars in the field log:
/// - Toyota Vitz DBA-NSP130 — Mode 0x01 PID 0x00 bitmap `BE 3F A8 13` (2026-05-28).
/// - Toyota Corolla Axio E160 — supported PIDs `01 03 04 05 06 07 0C 0D 0E 0F 10
///   11 13 15 1C 1F 20 21 24 2C 2E 30 31 33 34 3C 3E 40 42 43 44 45 46 47 49 4A
///   4C 4D 4E` (2026-05-29). The 0x33/0x3C/0x3E/0x44 entries below were added
///   from that scan. Polling a PID a given ECU doesn't support is harmless — the
///   poller just gets no response and round-robins on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum ObdPid {
    SupportedPids01To20 = 0x00,
    FuelSystemStatus = 0x03,
    EngineLoad = 0x04,
    CoolantTemperature = 0x05,
    ShortTermFuelTrimBank1 = 0x06,
    LongTermFuelTrimBank1 = 0x07,
    IntakeManifoldPressure = 0x0B,
    EngineRpm = 0x0C,
    VehicleSpeed = 0x0D,
    TimingAdvance = 0x0E,
    IntakeAirTemperature = 0x0F,
    MafAirFlowRate = 0x10,
    ThrottlePosition = 0x11,
    O2SensorsPresent = 0x13,
    O2SensorBank1Sensor2 = 0x15,
    RunTimeSinceStart = 0x1F,
    SupportedPids21To40 = 0x20,
    FuelTankLevel = 0x2F,
    BarometricPressure = 0x33,
    CatalystTempBank1Sensor1 = 0x3C,
    CatalystTempBank1Sensor2 = 0x3E,
    BatteryVoltage = 0x42,
    AbsoluteLoadValue = 0x43,
    CommandedEquivalenceRatio = 0x44,
    AmbientAirTemperature = 0x46,
    OilTemperature = 0x5C,
    EngineFuelRate = 0x5E,
}

impl ObdPid {
    /// Minimum number of data bytes the decoder needs.
    pub fn response_bytes(self) -> usize {
        match self {
            ObdPid::SupportedPids01To20 | ObdPid::SupportedPids21To40 => 4,
            ObdPid::EngineRpm
            | ObdPid::IntakeManifoldPressure   // 1 byte but read as A; 2 reserved
            | ObdPid::MafAirFlowRate
            | ObdPid::RunTimeSinceStart
            | ObdPid::BatteryVoltage
            | ObdPid::AbsoluteLoadValue
            | ObdPid::CatalystTempBank1Sensor1
            | ObdPid::CatalystTempBank1Sensor2
            | ObdPid::CommandedEquivalenceRatio
            | ObdPid::EngineFuelRate => 2,
            ObdPid::O2SensorBank1Sensor2 => 2,    // voltage A, STFT B
            _ => 1,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            ObdPid::SupportedPids01To20 => "Supported PIDs [01-20]",
            ObdPid::FuelSystemStatus => "Fuel System Status",
            ObdPid::EngineLoad => "Calculated Engine Load",
            ObdPid::CoolantTemperature => "Coolant Temperature",
            ObdPid::ShortTermFuelTrimBank1 => "Short-Term Fuel Trim B1",
            ObdPid::LongTermFuelTrimBank1 => "Long-Term Fuel Trim B1",
            ObdPid::IntakeManifoldPressure => "Intake Manifold Pressure",
            ObdPid::EngineRpm => "Engine RPM",
            ObdPid::VehicleSpeed => "Vehicle Speed",
            ObdPid::TimingAdvance => "Timing Advance",
            ObdPid::IntakeAirTemperature => "Intake Air Temperature",
            ObdPid::MafAirFlowRate => "MAF Air Flow Rate",
            ObdPid::ThrottlePosition => "Throttle Position",
            ObdPid::O2SensorsPresent => "O2 Sensors Present",
            ObdPid::O2SensorBank1Sensor2 => "O2 Sensor B1S2 Voltage",
            ObdPid::RunTimeSinceStart => "Run Time Since Start",
            ObdPid::SupportedPids21To40 => "Supported PIDs [21-40]",
            ObdPid::FuelTankLevel => "Fuel Tank Level",
            ObdPid::BarometricPressure => "Barometric Pressure",
            ObdPid::CatalystTempBank1Sensor1 => "Catalyst Temperature B1S1",
            ObdPid::CatalystTempBank1Sensor2 => "Catalyst Temperature B1S2",
            ObdPid::BatteryVoltage => "Battery / Control Module Voltage",
            ObdPid::AbsoluteLoadValue => "Absolute Load Value",
            ObdPid::CommandedEquivalenceRatio => "Commanded Equivalence Ratio (lambda)",
            ObdPid::AmbientAirTemperature => "Ambient Air Temperature",
            ObdPid::OilTemperature => "Engine Oil Temperature",
            ObdPid::EngineFuelRate => "Engine Fuel Rate",
        }
    }

    pub fn unit(self) -> &'static str {
        match self {
            ObdPid::SupportedPids01To20
            | ObdPid::SupportedPids21To40
            | ObdPid::FuelSystemStatus
            | ObdPid::O2SensorsPresent
            | ObdPid::CommandedEquivalenceRatio => "",
            ObdPid::CoolantTemperature
            | ObdPid::IntakeAirTemperature
            | ObdPid::AmbientAirTemperature
            | ObdPid::OilTemperature
            | ObdPid::CatalystTempBank1Sensor1
            | ObdPid::CatalystTempBank1Sensor2 => "°C",
            ObdPid::EngineRpm => "rpm",
            ObdPid::VehicleSpeed => "km/h",
            ObdPid::ThrottlePosition
            | ObdPid::FuelTankLevel
            | ObdPid::EngineLoad
            | ObdPid::ShortTermFuelTrimBank1
            | ObdPid::LongTermFuelTrimBank1
            | ObdPid::AbsoluteLoadValue => "%",
            ObdPid::IntakeManifoldPressure | ObdPid::BarometricPressure => "kPa",
            ObdPid::TimingAdvance => "° BTDC",
            ObdPid::MafAirFlowRate => "g/s",
            ObdPid::O2SensorBank1Sensor2 => "V",
            ObdPid::RunTimeSinceStart => "s",
            ObdPid::BatteryVoltage => "V",
            ObdPid::EngineFuelRate => "L/h",
        }
    }
}

/// Decoded OBD-II value.
#[derive(Debug, Clone)]
pub struct DecodedValue {
    pub pid: ObdPid,
    pub value: f64,
    pub unit: &'static str,
    pub raw: Vec<u8>,
}

impl fmt::Display for DecodedValue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.unit.is_empty() {
            write!(f, "{}: {:.2}", self.pid.name(), self.value)
        } else {
            write!(f, "{}: {:.2} {}", self.pid.name(), self.value, self.unit)
        }
    }
}

/// OBD-II errors.
#[derive(Debug)]
pub enum ObdError {
    ResponseTooShort { expected: usize, actual: usize },
    ModeMismatch { expected: u8, actual: u8 },
    PidMismatch { expected: u8, actual: u8 },
    NegativeResponse { service: u8, error_code: u8 },
    #[allow(dead_code)]
    PidNotSupported(ObdPid),
    #[allow(dead_code)]
    UnknownPid(u8),
}

impl fmt::Display for ObdError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ObdError::ResponseTooShort { expected, actual } => {
                write!(f, "Response too short: expected {}, got {}", expected, actual)
            }
            ObdError::ModeMismatch { expected, actual } => {
                write!(f, "Mode mismatch: expected 0x{:02X}, got 0x{:02X}", expected, actual)
            }
            ObdError::PidMismatch { expected, actual } => {
                write!(f, "PID mismatch: expected 0x{:02X}, got 0x{:02X}", expected, actual)
            }
            ObdError::NegativeResponse { service, error_code } => {
                write!(f, "Negative response: service 0x{:02X}, error 0x{:02X}", service, error_code)
            }
            ObdError::PidNotSupported(pid) => write!(f, "PID {:?} not supported", pid),
            ObdError::UnknownPid(pid) => write!(f, "Unknown PID: 0x{:02X}", pid),
        }
    }
}

impl std::error::Error for ObdError {}

pub type ObdResult<T> = Result<T, ObdError>;

/// OBD-II request builder.
#[derive(Debug, Clone)]
pub struct ObdRequest {
    pub service: ObdService,
    pub pid: ObdPid,
}

impl ObdRequest {
    pub fn current_data(pid: ObdPid) -> Self {
        Self { service: ObdService::CurrentData, pid }
    }

    pub fn to_can_data(&self) -> [u8; 8] {
        IsoTpSession::build_single_frame(&[self.service as u8, self.pid as u8])
    }

    pub fn can_id(&self) -> u32 {
        addressing::OBD_REQUEST_ID
    }
}

/// Helper to build a Mode 0x03 (read stored DTCs) request frame.
pub fn build_stored_dtc_request() -> ([u8; 8], u32) {
    let frame = IsoTpSession::build_single_frame(&[ObdService::StoredDtcs as u8]);
    (frame, addressing::OBD_REQUEST_ID)
}

/// Helper to build a Mode 0x09 (vehicle info) request frame for the given
/// info-type PID (e.g. 0x02 for VIN, 0x04 for calibration ID).
pub fn build_vehicle_info_request(info_pid: u8) -> ([u8; 8], u32) {
    let frame = IsoTpSession::build_single_frame(&[ObdService::VehicleInfo as u8, info_pid]);
    (frame, addressing::OBD_REQUEST_ID)
}

/// OBD-II response parser.
#[derive(Debug, Clone)]
pub struct ObdResponse {
    pub ecu_id: u32,
    pub service: u8,
    pub pid: u8,
    pub data: Vec<u8>,
}

impl ObdResponse {
    pub fn parse(ecu_id: u32, payload: &[u8]) -> ObdResult<Self> {
        if payload.len() < 2 {
            return Err(ObdError::ResponseTooShort { expected: 2, actual: payload.len() });
        }

        let mode = payload[0];
        if mode == 0x7F && payload.len() >= 3 {
            return Err(ObdError::NegativeResponse { service: payload[1], error_code: payload[2] });
        }

        Ok(Self {
            ecu_id,
            service: mode.saturating_sub(0x40),
            pid: payload[1],
            data: payload[2..].to_vec(),
        })
    }

    pub fn validate(&self, request: &ObdRequest) -> ObdResult<()> {
        let expected_mode = request.service.response_mode();
        let actual_mode = self.service + 0x40;
        if actual_mode != expected_mode {
            return Err(ObdError::ModeMismatch { expected: expected_mode, actual: actual_mode });
        }
        if self.pid != request.pid as u8 {
            return Err(ObdError::PidMismatch { expected: request.pid as u8, actual: self.pid });
        }
        Ok(())
    }

    pub fn decode(&self, pid: ObdPid) -> ObdResult<DecodedValue> {
        let expected = pid.response_bytes();
        if self.data.len() < expected {
            return Err(ObdError::ResponseTooShort { expected, actual: self.data.len() });
        }

        let a = self.data[0] as f64;
        let b = if self.data.len() > 1 { self.data[1] as f64 } else { 0.0 };

        // Formulas: SAE J1979 / ISO 15031-5.
        let value = match pid {
            ObdPid::SupportedPids01To20 | ObdPid::SupportedPids21To40 => {
                let c = self.data[2] as f64;
                let d = self.data[3] as f64;
                (a * 16777216.0) + (b * 65536.0) + (c * 256.0) + d
            }
            ObdPid::FuelSystemStatus | ObdPid::O2SensorsPresent => a,
            ObdPid::EngineLoad
            | ObdPid::ThrottlePosition
            | ObdPid::FuelTankLevel => a * 100.0 / 255.0,
            ObdPid::AbsoluteLoadValue => (a * 256.0 + b) * 100.0 / 255.0,
            ObdPid::ShortTermFuelTrimBank1 | ObdPid::LongTermFuelTrimBank1 => {
                (a - 128.0) * 100.0 / 128.0
            }
            ObdPid::IntakeManifoldPressure | ObdPid::BarometricPressure => a, // kPa absolute
            ObdPid::EngineRpm => (a * 256.0 + b) / 4.0,
            ObdPid::VehicleSpeed => a,
            ObdPid::TimingAdvance => (a - 128.0) / 2.0,
            ObdPid::CoolantTemperature
            | ObdPid::IntakeAirTemperature
            | ObdPid::AmbientAirTemperature
            | ObdPid::OilTemperature => a - 40.0,
            ObdPid::CatalystTempBank1Sensor1
            | ObdPid::CatalystTempBank1Sensor2 => (a * 256.0 + b) / 10.0 - 40.0,
            ObdPid::MafAirFlowRate => (a * 256.0 + b) / 100.0,
            ObdPid::O2SensorBank1Sensor2 => a / 200.0,           // voltage; STFT in B
            ObdPid::RunTimeSinceStart => a * 256.0 + b,
            ObdPid::BatteryVoltage => (a * 256.0 + b) / 1000.0,  // mV → V
            ObdPid::CommandedEquivalenceRatio => (a * 256.0 + b) / 32768.0, // lambda
            ObdPid::EngineFuelRate => (a * 256.0 + b) / 20.0,
        };

        Ok(DecodedValue { pid, value, unit: pid.unit(), raw: self.data.clone() })
    }
}

/// Parses the supported PIDs bitmask.
pub fn parse_supported_pids(data: &[u8]) -> Vec<u8> {
    if data.len() < 4 { return Vec::new(); }
    let bitmask = u32::from_be_bytes([data[0], data[1], data[2], data[3]]);
    (0..32).filter(|i| bitmask & (1 << (31 - i)) != 0).map(|i| i + 1).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_rpm() {
        let response = ObdResponse { ecu_id: 0x7E8, service: 0x01, pid: 0x0C, data: vec![0x2E, 0xE0] };
        let decoded = response.decode(ObdPid::EngineRpm).unwrap();
        assert_eq!(decoded.value, 3000.0);
    }

    #[test]
    fn test_decode_maf() {
        // 0x0A 0x41 = (10*256 + 65)/100 = 26.25 g/s
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x10, data: vec![0x0A, 0x41],
        };
        let decoded = response.decode(ObdPid::MafAirFlowRate).unwrap();
        assert!((decoded.value - 26.25).abs() < 0.001);
    }

    #[test]
    fn test_decode_engine_load() {
        // 0x80 = 128/255*100 ≈ 50.196 %
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x04, data: vec![0x80],
        };
        let decoded = response.decode(ObdPid::EngineLoad).unwrap();
        assert!((decoded.value - 50.196).abs() < 0.01);
    }

    #[test]
    fn test_decode_fuel_trim_zero() {
        // 0x80 = no correction (0%)
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x06, data: vec![0x80],
        };
        let decoded = response.decode(ObdPid::ShortTermFuelTrimBank1).unwrap();
        assert!((decoded.value - 0.0).abs() < 0.01);
    }

    #[test]
    fn test_decode_battery() {
        // 0x2F 0xC0 = 12224 / 1000 = 12.224 V (typical engine-running)
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x42, data: vec![0x2F, 0xC0],
        };
        let decoded = response.decode(ObdPid::BatteryVoltage).unwrap();
        assert!((decoded.value - 12.224).abs() < 0.001);
    }

    #[test]
    fn test_decode_catalyst_temp() {
        // Real Axio reading 2026-05-29: raw 08 C8 → 0x08C8 = 2248 → 2248/10 - 40 = 184.8 °C
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x3C, data: vec![0x08, 0xC8],
        };
        let decoded = response.decode(ObdPid::CatalystTempBank1Sensor1).unwrap();
        assert!((decoded.value - 184.8).abs() < 0.001);
    }

    #[test]
    fn test_decode_commanded_lambda() {
        // 0x80 0x00 = 32768 / 32768 = 1.0 (stoichiometric)
        let response = ObdResponse {
            ecu_id: 0x7E8, service: 0x01, pid: 0x44, data: vec![0x80, 0x00],
        };
        let decoded = response.decode(ObdPid::CommandedEquivalenceRatio).unwrap();
        assert!((decoded.value - 1.0).abs() < 0.0001);
    }

    #[test]
    fn test_is_obd_response() {
        assert!(addressing::is_obd_response(0x7E8));
        assert!(!addressing::is_obd_response(0x7DF));
    }
}
