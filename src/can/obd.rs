//! OBD-II protocol implementation.
//!
//! Implements ISO 15031 / SAE J1979 over CAN bus.

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
    CurrentData = 0x01,
}

impl ObdService {
    pub fn response_mode(self) -> u8 {
        (self as u8) + 0x40
    }
}

/// OBD-II Parameter ID (PID) definitions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum ObdPid {
    SupportedPids01To20 = 0x00,
    CoolantTemperature = 0x05,
    EngineRpm = 0x0C,
    VehicleSpeed = 0x0D,
    IntakeAirTemperature = 0x0F,
    ThrottlePosition = 0x11,
    SupportedPids21To40 = 0x20,
    FuelTankLevel = 0x2F,
}

impl ObdPid {
    pub fn response_bytes(self) -> usize {
        match self {
            ObdPid::SupportedPids01To20 | ObdPid::SupportedPids21To40 => 4,
            ObdPid::EngineRpm => 2,
            _ => 1,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            ObdPid::SupportedPids01To20 => "Supported PIDs [01-20]",
            ObdPid::CoolantTemperature => "Coolant Temperature",
            ObdPid::EngineRpm => "Engine RPM",
            ObdPid::VehicleSpeed => "Vehicle Speed",
            ObdPid::IntakeAirTemperature => "Intake Air Temperature",
            ObdPid::ThrottlePosition => "Throttle Position",
            ObdPid::SupportedPids21To40 => "Supported PIDs [21-40]",
            ObdPid::FuelTankLevel => "Fuel Tank Level",
        }
    }

    pub fn unit(self) -> &'static str {
        match self {
            ObdPid::SupportedPids01To20 | ObdPid::SupportedPids21To40 => "",
            ObdPid::CoolantTemperature | ObdPid::IntakeAirTemperature => "°C",
            ObdPid::EngineRpm => "rpm",
            ObdPid::VehicleSpeed => "km/h",
            ObdPid::ThrottlePosition | ObdPid::FuelTankLevel => "%",
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

        let value = match pid {
            ObdPid::SupportedPids01To20 | ObdPid::SupportedPids21To40 => {
                let (a, b, c, d) = (self.data[0] as f64, self.data[1] as f64, self.data[2] as f64, self.data[3] as f64);
                (a * 16777216.0) + (b * 65536.0) + (c * 256.0) + d
            }
            ObdPid::EngineRpm => ((self.data[0] as f64 * 256.0) + self.data[1] as f64) / 4.0,
            ObdPid::VehicleSpeed => self.data[0] as f64,
            ObdPid::CoolantTemperature | ObdPid::IntakeAirTemperature => self.data[0] as f64 - 40.0,
            ObdPid::ThrottlePosition | ObdPid::FuelTankLevel => (self.data[0] as f64 * 100.0) / 255.0,
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
    fn test_is_obd_response() {
        assert!(addressing::is_obd_response(0x7E8));
        assert!(!addressing::is_obd_response(0x7DF));
    }
}
