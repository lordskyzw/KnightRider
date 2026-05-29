//! Passive CAN sniffer with a hardcoded Toyota Prius/Auris-platform DBC.
//!
//! Listens to every broadcast CAN frame on its interface, looks the frame ID
//! up in [`PROFILE`], and emits one [`Sample`] per matched signal.
//!
//! Why a hardcoded profile: the production target is "load opendbc DBC files
//! at runtime per car", but for the field tests the cars we can hit are a
//! Toyota Vitz DBA-NSP130 (shares the E-platform CAN layout with the Toyota
//! Prius 2010 DBC in opendbc) and a Toyota Corolla Axio E160. Hardcoding the
//! frames we've verified via candump (Vitz 2026-05-28, Axio 2026-05-29 — see
//! `captures/axio-re-20260529/FINDINGS.md`) gets us a 25× rate bump on RPM
//! (≈42 Hz vs the OBD poller's ≈1.7 Hz) plus live boolean body signals
//! (brake, door) without waiting for the generic DBC loader.
//!
//! The sniffer needs its **own** CAN socket, separate from the poller's:
//! SocketCAN sockets each receive a private copy of every frame on the
//! interface, so the two threads don't interfere. The sniffer skips frames
//! in the OBD-II address window so it doesn't double-count what the poller
//! is already decoding.

use std::thread::JoinHandle;
use std::time::Duration;

use crate::can::interface::CanError;
use crate::can::CanInterface;
use crate::extractor::sample::{Sample, SampleSource};
use crate::extractor::SampleSender;

/// How to pull a raw integer out of a CAN frame's data bytes.
#[derive(Debug, Clone, Copy)]
enum Field {
    /// Big-endian (Motorola / DBC `@0`) byte-aligned value: `data[hi ..= hi+len-1]`.
    Bytes { hi: usize, len: usize },
    /// A single bit — bit `bit` (0 = LSB) of byte `byte`. Raw value is 0 or 1.
    /// Needed for boolean body signals (brake switch, door ajar) that the old
    /// byte-aligned-only decoder couldn't express.
    Bit { byte: usize, bit: u8 },
}

/// One decodable signal inside a CAN frame. `raw * scale + offset` → value.
#[derive(Debug, Clone, Copy)]
struct Signal {
    name: &'static str,
    field: Field,
    scale: f64,
    offset: f64,
    unit: &'static str,
}

#[derive(Debug, Clone, Copy)]
struct Message {
    id: u32,
    name: &'static str,
    signals: &'static [Signal],
}

/// Hardcoded Toyota CAN profile. Frame IDs are car-specific, so this is keyed
/// by ID and simply additive: a frame is decoded only if its exact ID appears
/// here, so the Prius/Vitz powertrain set and the Axio body set coexist without
/// collision (e.g. the Axio has no `0x1C4`/`0x0AA`, the Prius set just never
/// matches there). The production path remains "load opendbc per car at
/// runtime"; this is the field-verified stopgap.
const PROFILE: &[Message] = &[
    // --- Toyota Prius 2010 powertrain DBC subset (observed on the Vitz) ---
    Message {
        id: 0x1C4,
        name: "POWERTRAIN",
        signals: &[Signal {
            name: "engine_rpm",
            field: Field::Bytes { hi: 0, len: 2 },
            scale: 1.0,
            offset: 0.0,
            unit: "rpm",
        }],
    },
    Message {
        id: 0x0AA,
        name: "WHEEL_SPEEDS",
        signals: &[
            Signal { name: "wheel_speed_fr", field: Field::Bytes { hi: 0, len: 2 },
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_fl", field: Field::Bytes { hi: 2, len: 2 },
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_rr", field: Field::Bytes { hi: 4, len: 2 },
                     scale: 0.0062, offset: -67.67, unit: "mph" },
            Signal { name: "wheel_speed_rl", field: Field::Bytes { hi: 6, len: 2 },
                     scale: 0.0062, offset: -67.67, unit: "mph" },
        ],
    },
    Message {
        id: 0x0B4,
        name: "SPEED",
        signals: &[Signal {
            // DBC says SPEED is at 47|16@0+ which is bytes 5..6 inclusive
            // (bit 47 = byte 5 MSB).
            name: "speed",
            field: Field::Bytes { hi: 5, len: 2 },
            scale: 0.0062,
            offset: 0.0,
            unit: "mph",
        }],
    },
    // --- Toyota Corolla Axio E160, reverse-engineered 2026-05-29 via
    //     baseline-vs-active candump diff (captures/axio-re-20260529/FINDINGS.md).
    //     These are boolean body signals; exterior lighting (turn/headlight) is
    //     NOT on the OBD-II bus (gatewayed off) so it can't be added here. ---
    Message {
        id: 0x224,
        name: "BRAKE",
        signals: &[Signal {
            // byte0 bit5: 0x00 (released) → 0x20 (pressed), 41 Hz powertrain switch.
            name: "pressed",
            field: Field::Bit { byte: 0, bit: 5 },
            scale: 1.0,
            offset: 0.0,
            unit: "",
        }],
    },
    Message {
        id: 0x3B4,
        name: "STOP_LAMP",
        signals: &[Signal {
            // byte4 bit0: stop-lamp echo on the body ECU, set with the brake.
            name: "on",
            field: Field::Bit { byte: 4, bit: 0 },
            scale: 1.0,
            offset: 0.0,
            unit: "",
        }],
    },
    Message {
        id: 0x620,
        name: "DOORS",
        signals: &[Signal {
            // byte5 bit5: driver door — 0x40 (closed) → 0x60 (open).
            name: "driver",
            field: Field::Bit { byte: 5, bit: 5 },
            scale: 1.0,
            offset: 0.0,
            unit: "",
        }],
    },
];

/// True if the frame ID falls inside the OBD-II diagnostic range — the
/// poller is already publishing those, and we don't want duplicates.
fn is_obd_window(id: u32) -> bool {
    (0x7DF..=0x7EF).contains(&id)
}

/// Spawns the sniffer on a dedicated thread. `can` is moved in.
pub fn spawn(can: CanInterface, tx: SampleSender) -> JoinHandle<()> {
    std::thread::Builder::new()
        .name("can-sniffer".into())
        .spawn(move || run(can, tx))
        .expect("spawn can-sniffer thread")
}

fn run(can: CanInterface, tx: SampleSender) {
    log::info!(
        "can-sniffer: starting · profile = Toyota (Prius PT + Axio body) · {} message(s)",
        PROFILE.len()
    );

    let mut frames_seen: u64 = 0;
    let mut frames_decoded: u64 = 0;
    let mut last_log = std::time::Instant::now();

    loop {
        match can.recv() {
            Ok(frame) => {
                frames_seen += 1;
                if is_obd_window(frame.id) {
                    continue;
                }
                if let Some(msg) = PROFILE.iter().find(|m| m.id == frame.id) {
                    decode_and_emit(msg, &frame, &tx);
                    frames_decoded += 1;
                }
            }
            Err(CanError::Timeout) => {}
            Err(e) => {
                log::warn!("can-sniffer: recv error: {} — backing off", e);
                std::thread::sleep(Duration::from_millis(200));
            }
        }

        // Periodic stats line so the logs show whether the sniffer is alive.
        if last_log.elapsed() >= Duration::from_secs(30) {
            log::info!(
                "can-sniffer: seen={} decoded={}",
                frames_seen, frames_decoded
            );
            last_log = std::time::Instant::now();
        }
    }
}

/// Pull the raw integer for a signal out of the frame's data, or `None` if the
/// frame is too short for it.
fn extract_raw(field: Field, data: &[u8]) -> Option<u32> {
    match field {
        Field::Bytes { hi, len } => {
            let end = hi + len;
            if end > data.len() {
                return None;
            }
            Some(data[hi..end].iter().fold(0u32, |acc, &b| (acc << 8) | b as u32))
        }
        Field::Bit { byte, bit } => {
            if byte >= data.len() || bit > 7 {
                return None;
            }
            Some(((data[byte] >> bit) & 1) as u32)
        }
    }
}

fn decode_and_emit(msg: &Message, frame: &crate::can::CanFrame, tx: &SampleSender) {
    let data = frame.data();
    for sig in msg.signals {
        let Some(raw) = extract_raw(sig.field, data) else {
            continue;
        };
        let value = raw as f64 * sig.scale + sig.offset;
        let signal = format!("dbc.toyota.{}.{}", msg.name, sig.name);
        let _ = tx.send(Sample::new(
            SampleSource::Sniffer,
            signal,
            value,
            sig.unit,
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bytes_field_is_big_endian() {
        let data = [0x12, 0x34, 0x56, 0x78];
        assert_eq!(extract_raw(Field::Bytes { hi: 0, len: 2 }, &data), Some(0x1234));
        assert_eq!(extract_raw(Field::Bytes { hi: 1, len: 1 }, &data), Some(0x34));
        // too short
        assert_eq!(extract_raw(Field::Bytes { hi: 3, len: 2 }, &data), None);
    }

    #[test]
    fn bit_field_extracts_single_bit() {
        // Axio brake: byte0 bit5 — 0x20 pressed, 0x00 released.
        assert_eq!(extract_raw(Field::Bit { byte: 0, bit: 5 }, &[0x20]), Some(1));
        assert_eq!(extract_raw(Field::Bit { byte: 0, bit: 5 }, &[0x00]), Some(0));
        // Axio driver door: byte5 bit5 — 0x60 open vs 0x40 closed.
        assert_eq!(extract_raw(Field::Bit { byte: 5, bit: 5 }, &[0, 0, 0, 0, 0, 0x60]), Some(1));
        assert_eq!(extract_raw(Field::Bit { byte: 5, bit: 5 }, &[0, 0, 0, 0, 0, 0x40]), Some(0));
        // byte out of range
        assert_eq!(extract_raw(Field::Bit { byte: 5, bit: 5 }, &[0x20]), None);
    }
}
