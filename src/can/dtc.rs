//! OBD-II Mode 0x03 / 0x07 / 0x0A Diagnostic Trouble Code (DTC) parser.
//!
//! All three modes return the same code-list format:
//!
//! ```text
//! 43 NN  D1H D1L  D2H D2L  D3H D3L
//!        └── DTC1 ──┘  └── DTC2 ──┘  ...
//! ```
//!
//! Where `NN` is the number of DTCs and each subsequent 2 bytes encode one
//! code. A single CAN frame can carry up to 3 DTCs; more requires multi-frame
//! ISO-TP. The two high bits of byte 0 of each DTC select the system
//! (P/B/C/U) and the remaining 14 bits are the hex code.
//!
//! This module is strictly read-only. It does **not** implement Mode 0x04
//! (clear DTCs), which would write to the ECU.

use std::fmt;

/// DTC system prefix.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DtcKind {
    /// Powertrain (engine, transmission, related).
    Powertrain,
    /// Chassis (ABS, suspension, steering, …).
    Chassis,
    /// Body (airbags, central locking, immobiliser, …).
    Body,
    /// Network communication (CAN, LIN, …).
    Network,
}

impl DtcKind {
    pub fn prefix(self) -> char {
        match self {
            DtcKind::Powertrain => 'P',
            DtcKind::Chassis => 'C',
            DtcKind::Body => 'B',
            DtcKind::Network => 'U',
        }
    }

    fn from_top_bits(bits: u8) -> Self {
        match bits & 0b11 {
            0b00 => DtcKind::Powertrain,
            0b01 => DtcKind::Chassis,
            0b10 => DtcKind::Body,
            0b11 => DtcKind::Network,
            _ => unreachable!(),
        }
    }
}

/// A parsed diagnostic trouble code (e.g. P0301).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct DtcCode {
    pub kind: DtcKind,
    /// The lower 14 bits packed into a u16. `Display` renders this as 4 hex
    /// digits with the system prefix character — e.g. `P0301`, `U0073`.
    pub code: u16,
}

impl DtcCode {
    /// Decode a 2-byte DTC field.
    ///
    /// Returns `None` if the field is `0x0000` (which OBD-II uses as a
    /// "no DTC" sentinel inside otherwise-empty response slots).
    pub fn from_bytes(high: u8, low: u8) -> Option<Self> {
        if high == 0 && low == 0 {
            return None;
        }
        let kind = DtcKind::from_top_bits(high >> 6);
        let code = (((high & 0x3F) as u16) << 8) | (low as u16);
        Some(Self { kind, code })
    }

    /// Canonical lowercase representation (`p0301`) — suitable for use as a
    /// signal name in the sample stream.
    pub fn as_signal(self) -> String {
        format!("{}{:04x}", self.kind.prefix().to_ascii_lowercase(), self.code)
    }
}

impl fmt::Display for DtcCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}{:04X}", self.kind.prefix(), self.code)
    }
}

/// Parse a Mode 0x03 / 0x07 / 0x0A response payload (without the leading
/// service-mode echo byte) into a list of DTCs.
///
/// Expected layout:
///
/// ```text
/// payload = [count, dtc1_high, dtc1_low, dtc2_high, dtc2_low, ...]
/// ```
///
/// The first byte is the number of DTCs the ECU is reporting. Each
/// subsequent pair is one code. Trailing `0x0000` slots used as padding are
/// silently dropped.
pub fn parse_dtc_list(payload: &[u8]) -> Vec<DtcCode> {
    if payload.is_empty() {
        return Vec::new();
    }
    let claimed = payload[0] as usize;
    let bytes = &payload[1..];

    let mut out = Vec::with_capacity(claimed);
    for chunk in bytes.chunks_exact(2) {
        if let Some(dtc) = DtcCode::from_bytes(chunk[0], chunk[1]) {
            out.push(dtc);
        }
    }
    // If the ECU under-reported (sometimes happens at IG-ON), we still
    // return everything non-zero we actually decoded.
    if out.len() > claimed && claimed > 0 {
        out.truncate(claimed);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_p0301() {
        let d = DtcCode::from_bytes(0x03, 0x01).unwrap();
        assert_eq!(d.kind, DtcKind::Powertrain);
        assert_eq!(d.code, 0x0301);
        assert_eq!(format!("{}", d), "P0301");
        assert_eq!(d.as_signal(), "p0301");
    }

    #[test]
    fn parse_u0073() {
        // U0073 — 0b11_000000 0x73 → high=0xC0, low=0x73
        let d = DtcCode::from_bytes(0xC0, 0x73).unwrap();
        assert_eq!(d.kind, DtcKind::Network);
        assert_eq!(format!("{}", d), "U0073");
    }

    #[test]
    fn parse_b0001() {
        // 0b10_xxxxxx — Body
        let d = DtcCode::from_bytes(0x80, 0x01).unwrap();
        assert_eq!(d.kind, DtcKind::Body);
        assert_eq!(format!("{}", d), "B0001");
    }

    #[test]
    fn no_dtc_zero_field() {
        assert!(DtcCode::from_bytes(0x00, 0x00).is_none());
    }

    #[test]
    fn parse_list_two_dtcs() {
        // count=2, [P0301, U0073]
        let payload = [0x02, 0x03, 0x01, 0xC0, 0x73, 0x00, 0x00];
        let list = parse_dtc_list(&payload);
        assert_eq!(list.len(), 2);
        assert_eq!(format!("{}", list[0]), "P0301");
        assert_eq!(format!("{}", list[1]), "U0073");
    }

    #[test]
    fn parse_list_no_dtcs() {
        let payload = [0x00];
        let list = parse_dtc_list(&payload);
        assert!(list.is_empty());
    }
}
