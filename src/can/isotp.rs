//! ISO-TP (ISO 15765-2) multi-frame message handling.
//!
//! ISO-TP is the transport protocol used for OBD-II messages that exceed
//! the 7-byte CAN payload limit. It handles message segmentation and
//! reassembly.
//!
//! # Frame Types
//!
//! | Type | First Nibble | Description |
//! |------|--------------|-------------|
//! | Single Frame (SF) | 0x0 | Complete message ≤7 bytes |
//! | First Frame (FF) | 0x1 | First fragment of multi-frame |
//! | Consecutive Frame (CF) | 0x2 | Subsequent fragments |
//! | Flow Control (FC) | 0x3 | Receiver → Sender flow control |
//!
//! # OBD-II Usage
//!
//! Most OBD-II responses fit in a single frame (7 bytes or less), but
//! some responses (like supported PIDs bitmask) may require multi-frame.

use std::fmt;

/// Maximum ISO-TP message size we support.
/// OBD-II responses are typically small, 256 bytes is more than enough.
const MAX_MESSAGE_SIZE: usize = 256;

/// ISO-TP frame type, determined by the first nibble of the first byte.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrameType {
    /// Single Frame - complete message in one CAN frame (≤7 bytes)
    SingleFrame,
    /// First Frame - first fragment of a multi-frame message
    FirstFrame,
    /// Consecutive Frame - subsequent fragments
    ConsecutiveFrame,
    /// Flow Control - tells sender how to proceed
    FlowControl,
}

impl FrameType {
    /// Determines the frame type from the first byte of CAN data.
    pub fn from_byte(byte: u8) -> Option<Self> {
        match byte >> 4 {
            0x0 => Some(FrameType::SingleFrame),
            0x1 => Some(FrameType::FirstFrame),
            0x2 => Some(FrameType::ConsecutiveFrame),
            0x3 => Some(FrameType::FlowControl),
            _ => None,
        }
    }
}

/// Flow control status codes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FlowStatus {
    /// Clear To Send - continue sending
    ClearToSend = 0,
    /// Wait - pause sending
    Wait = 1,
    /// Overflow - abort, receiver buffer full
    Overflow = 2,
}

/// Errors that can occur during ISO-TP processing.
#[derive(Debug)]
pub enum IsoTpError {
    /// Frame is too short to be valid.
    FrameTooShort,
    /// Invalid frame type nibble.
    InvalidFrameType(u8),
    /// Unexpected frame sequence number.
    SequenceError { expected: u8, received: u8 },
    /// Message exceeds maximum supported size.
    MessageTooLong(usize),
    /// Timeout waiting for frames.
    Timeout,
    /// Flow control indicated overflow.
    Overflow,
    /// Incomplete multi-frame message.
    Incomplete,
}

impl fmt::Display for IsoTpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IsoTpError::FrameTooShort => write!(f, "ISO-TP frame too short"),
            IsoTpError::InvalidFrameType(b) => write!(f, "Invalid ISO-TP frame type: 0x{:02X}", b),
            IsoTpError::SequenceError { expected, received } => {
                write!(
                    f,
                    "ISO-TP sequence error: expected {}, got {}",
                    expected, received
                )
            }
            IsoTpError::MessageTooLong(size) => {
                write!(f, "ISO-TP message too long: {} bytes", size)
            }
            IsoTpError::Timeout => write!(f, "ISO-TP timeout"),
            IsoTpError::Overflow => write!(f, "ISO-TP receiver overflow"),
            IsoTpError::Incomplete => write!(f, "ISO-TP message incomplete"),
        }
    }
}

impl std::error::Error for IsoTpError {}

/// Result type for ISO-TP operations.
pub type IsoTpResult<T> = Result<T, IsoTpError>;

/// Parsed ISO-TP frame data.
#[derive(Debug, Clone)]
pub enum IsoTpFrame {
    /// Single Frame with complete payload.
    Single { data: Vec<u8> },
    /// First Frame of multi-frame message.
    First {
        total_length: usize,
        data: Vec<u8>,
    },
    /// Consecutive Frame with sequence number.
    Consecutive { sequence: u8, data: Vec<u8> },
    /// Flow Control frame.
    FlowControl {
        status: FlowStatus,
        block_size: u8,
        separation_time: u8,
    },
}

impl IsoTpFrame {
    /// Parses raw CAN frame data into an ISO-TP frame.
    ///
    /// # Arguments
    ///
    /// * `data` - Raw CAN frame data (up to 8 bytes)
    ///
    /// # Errors
    ///
    /// Returns an error if the frame is malformed.
    pub fn parse(data: &[u8]) -> IsoTpResult<Self> {
        if data.is_empty() {
            return Err(IsoTpError::FrameTooShort);
        }

        let frame_type = FrameType::from_byte(data[0])
            .ok_or(IsoTpError::InvalidFrameType(data[0]))?;

        match frame_type {
            FrameType::SingleFrame => {
                let length = (data[0] & 0x0F) as usize;
                if data.len() < length + 1 {
                    return Err(IsoTpError::FrameTooShort);
                }
                Ok(IsoTpFrame::Single {
                    data: data[1..1 + length].to_vec(),
                })
            }

            FrameType::FirstFrame => {
                if data.len() < 2 {
                    return Err(IsoTpError::FrameTooShort);
                }
                // Total length is 12 bits: lower 4 bits of byte 0 + all of byte 1
                let total_length = (((data[0] & 0x0F) as usize) << 8) | (data[1] as usize);
                if total_length > MAX_MESSAGE_SIZE {
                    return Err(IsoTpError::MessageTooLong(total_length));
                }
                // First frame carries first 6 bytes of data
                let payload_len = (data.len() - 2).min(6);
                Ok(IsoTpFrame::First {
                    total_length,
                    data: data[2..2 + payload_len].to_vec(),
                })
            }

            FrameType::ConsecutiveFrame => {
                let sequence = data[0] & 0x0F;
                Ok(IsoTpFrame::Consecutive {
                    sequence,
                    data: data[1..].to_vec(),
                })
            }

            FrameType::FlowControl => {
                if data.len() < 3 {
                    return Err(IsoTpError::FrameTooShort);
                }
                let status = match data[0] & 0x0F {
                    0 => FlowStatus::ClearToSend,
                    1 => FlowStatus::Wait,
                    2 => FlowStatus::Overflow,
                    _ => return Err(IsoTpError::InvalidFrameType(data[0])),
                };
                Ok(IsoTpFrame::FlowControl {
                    status,
                    block_size: data[1],
                    separation_time: data[2],
                })
            }
        }
    }
}

/// ISO-TP session for reassembling multi-frame messages.
///
/// This handles the state machine for receiving multi-frame messages.
/// For single-frame messages, it returns immediately.
#[derive(Debug)]
pub struct IsoTpSession {
    /// Buffer for reassembling multi-frame messages.
    buffer: Vec<u8>,
    /// Expected total length for multi-frame messages.
    expected_length: usize,
    /// Expected next sequence number (0-15, wraps).
    next_sequence: u8,
    /// True if we're in the middle of receiving a multi-frame message.
    receiving: bool,
}

impl Default for IsoTpSession {
    fn default() -> Self {
        Self::new()
    }
}

impl IsoTpSession {
    /// Creates a new ISO-TP session.
    pub fn new() -> Self {
        Self {
            buffer: Vec::with_capacity(MAX_MESSAGE_SIZE),
            expected_length: 0,
            next_sequence: 1,
            receiving: false,
        }
    }

    /// Resets the session state.
    pub fn reset(&mut self) {
        self.buffer.clear();
        self.expected_length = 0;
        self.next_sequence = 1;
        self.receiving = false;
    }

    /// Returns true if we're in the middle of receiving a multi-frame message.
    pub fn is_receiving(&self) -> bool {
        self.receiving
    }

    /// Processes a received CAN frame and returns the complete message if ready.
    ///
    /// # Arguments
    ///
    /// * `data` - Raw CAN frame data
    ///
    /// # Returns
    ///
    /// * `Ok(Some(data))` - Complete message received
    /// * `Ok(None)` - Need more frames (multi-frame in progress)
    /// * `Err(...)` - Protocol error
    pub fn receive(&mut self, data: &[u8]) -> IsoTpResult<Option<Vec<u8>>> {
        let frame = IsoTpFrame::parse(data)?;

        match frame {
            IsoTpFrame::Single { data } => {
                // Single frame = complete message
                self.reset();
                Ok(Some(data))
            }

            IsoTpFrame::First { total_length, data } => {
                // Start of multi-frame message
                self.reset();
                self.expected_length = total_length;
                self.buffer.extend_from_slice(&data);
                self.receiving = true;
                self.next_sequence = 1;

                // TODO: In a real implementation, we would send a Flow Control
                // frame here. For OBD-II as a client, we just wait for CFs.
                
                Ok(None)
            }

            IsoTpFrame::Consecutive { sequence, data } => {
                if !self.receiving {
                    // Unexpected CF without FF
                    return Err(IsoTpError::SequenceError {
                        expected: 0,
                        received: sequence,
                    });
                }

                if sequence != self.next_sequence {
                    return Err(IsoTpError::SequenceError {
                        expected: self.next_sequence,
                        received: sequence,
                    });
                }

                // Add data to buffer (only up to expected length)
                let remaining = self.expected_length - self.buffer.len();
                let to_copy = remaining.min(data.len());
                self.buffer.extend_from_slice(&data[..to_copy]);

                // Update sequence (wraps at 16)
                self.next_sequence = (self.next_sequence + 1) & 0x0F;

                // Check if message is complete
                if self.buffer.len() >= self.expected_length {
                    let result = self.buffer.clone();
                    self.reset();
                    Ok(Some(result))
                } else {
                    Ok(None)
                }
            }

            IsoTpFrame::FlowControl { status, .. } => {
                // As a client (sending requests), we would use this to pace
                // our transmissions. For OBD-II Mode 01 requests, this is
                // typically not needed since requests are always single-frame.
                match status {
                    FlowStatus::Overflow => Err(IsoTpError::Overflow),
                    _ => Ok(None),
                }
            }
        }
    }

    /// Builds a Flow Control frame.
    ///
    /// Used when we're receiving a multi-frame message and need to tell
    /// the sender to continue.
    ///
    /// # Arguments
    ///
    /// * `block_size` - Number of CFs to send before waiting (0 = send all)
    /// * `separation_time` - Minimum time between CFs in ms (0 = no delay)
    #[allow(dead_code)]
    pub fn build_flow_control(block_size: u8, separation_time: u8) -> [u8; 8] {
        [
            0x30, // Flow Control, Clear To Send
            block_size,
            separation_time,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
    }

    /// Builds a Single Frame for sending a short message.
    ///
    /// # Arguments
    ///
    /// * `data` - Message data (must be ≤7 bytes)
    ///
    /// # Panics
    ///
    /// Panics if data length exceeds 7 bytes.
    pub fn build_single_frame(data: &[u8]) -> [u8; 8] {
        assert!(data.len() <= 7, "Single frame data cannot exceed 7 bytes");
        
        let mut frame = [0u8; 8];
        frame[0] = data.len() as u8; // SF with length
        frame[1..1 + data.len()].copy_from_slice(data);
        frame
    }
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_type_from_byte() {
        assert_eq!(FrameType::from_byte(0x04), Some(FrameType::SingleFrame));
        assert_eq!(FrameType::from_byte(0x10), Some(FrameType::FirstFrame));
        assert_eq!(FrameType::from_byte(0x21), Some(FrameType::ConsecutiveFrame));
        assert_eq!(FrameType::from_byte(0x30), Some(FrameType::FlowControl));
        assert_eq!(FrameType::from_byte(0x40), None);
    }

    #[test]
    fn test_parse_single_frame() {
        // OBD-II RPM response: 04 41 0C 2E E0 00 00 00
        let data = [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00];
        let frame = IsoTpFrame::parse(&data).unwrap();

        match frame {
            IsoTpFrame::Single { data } => {
                assert_eq!(data, vec![0x41, 0x0C, 0x2E, 0xE0]);
            }
            _ => panic!("Expected Single Frame"),
        }
    }

    #[test]
    fn test_parse_first_frame() {
        // First frame with 20 bytes total: 10 14 41 00 BE 1F B8 10
        let data = [0x10, 0x14, 0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10];
        let frame = IsoTpFrame::parse(&data).unwrap();

        match frame {
            IsoTpFrame::First { total_length, data } => {
                assert_eq!(total_length, 20);
                assert_eq!(data, vec![0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10]);
            }
            _ => panic!("Expected First Frame"),
        }
    }

    #[test]
    fn test_parse_consecutive_frame() {
        // Consecutive frame with sequence 1: 21 C0 00 00 00 00 00 00
        let data = [0x21, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        let frame = IsoTpFrame::parse(&data).unwrap();

        match frame {
            IsoTpFrame::Consecutive { sequence, data } => {
                assert_eq!(sequence, 1);
                assert_eq!(data, vec![0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]);
            }
            _ => panic!("Expected Consecutive Frame"),
        }
    }

    #[test]
    fn test_parse_flow_control() {
        // Standard OBD-II flow control: 30 00 00 00 00 00 00 00
        let data = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        let frame = IsoTpFrame::parse(&data).unwrap();

        match frame {
            IsoTpFrame::FlowControl {
                status,
                block_size,
                separation_time,
            } => {
                assert_eq!(status, FlowStatus::ClearToSend);
                assert_eq!(block_size, 0);
                assert_eq!(separation_time, 0);
            }
            _ => panic!("Expected Flow Control"),
        }
    }

    #[test]
    fn test_session_single_frame() {
        let mut session = IsoTpSession::new();
        let data = [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00];

        let result = session.receive(&data).unwrap();
        assert_eq!(result, Some(vec![0x41, 0x0C, 0x2E, 0xE0]));
        assert!(!session.is_receiving());
    }

    #[test]
    fn test_session_multi_frame() {
        let mut session = IsoTpSession::new();

        // First frame: total 10 bytes, first 6 bytes of data
        let ff = [0x10, 0x0A, 0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10];
        let result = session.receive(&ff).unwrap();
        assert!(result.is_none());
        assert!(session.is_receiving());

        // Consecutive frame 1: remaining 4 bytes
        let cf = [0x21, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        let result = session.receive(&cf).unwrap();

        // Should have complete message now
        assert!(result.is_some());
        let message = result.unwrap();
        assert_eq!(message.len(), 10);
        assert_eq!(&message[..6], &[0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10]);
        assert_eq!(&message[6..10], &[0xC0, 0x00, 0x00, 0x00]);
    }

    #[test]
    fn test_session_sequence_error() {
        let mut session = IsoTpSession::new();

        // First frame
        let ff = [0x10, 0x14, 0x41, 0x00, 0xBE, 0x1F, 0xB8, 0x10];
        session.receive(&ff).unwrap();

        // Wrong sequence (2 instead of 1)
        let cf = [0x22, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
        let result = session.receive(&cf);

        assert!(matches!(result, Err(IsoTpError::SequenceError { .. })));
    }

    #[test]
    fn test_build_single_frame() {
        let frame = IsoTpSession::build_single_frame(&[0x01, 0x0C]);
        assert_eq!(frame, [0x02, 0x01, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x00]);
    }

    #[test]
    #[should_panic(expected = "Single frame data cannot exceed 7 bytes")]
    fn test_build_single_frame_too_long() {
        let _ = IsoTpSession::build_single_frame(&[0; 8]);
    }
}
