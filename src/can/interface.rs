//! Raw SocketCAN interface.
//!
//! This module provides low-level send/receive operations for CAN frames
//! via the Linux SocketCAN interface. On non-Linux systems, this module
//! provides stub implementations for compilation purposes.
//!
//! # Architecture Decision
//!
//! We use blocking I/O with timeouts rather than async. This keeps the
//! implementation simple and avoids async runtime dependencies. The 100ms
//! polling rate is well within the capabilities of blocking I/O.

use std::fmt;
use std::io;
use std::time::Duration;

/// Standard CAN frame (CAN 2.0A/B).
///
/// Represents a single CAN frame with an 11-bit or 29-bit identifier
/// and up to 8 bytes of data.
#[derive(Clone, Default)]
pub struct CanFrame {
    /// CAN identifier (11-bit standard or 29-bit extended).
    pub id: u32,
    /// Data Length Code (0-8).
    pub dlc: u8,
    /// Frame data (up to 8 bytes).
    pub data: [u8; 8],
    /// True if this is an extended frame (29-bit ID).
    pub extended: bool,
}

impl CanFrame {
    /// Creates a new CAN frame with the given ID and data.
    ///
    /// # Arguments
    ///
    /// * `id` - CAN identifier (11-bit for standard, 29-bit for extended)
    /// * `data` - Frame payload (up to 8 bytes)
    ///
    /// # Panics
    ///
    /// Panics if data length exceeds 8 bytes.
    pub fn new(id: u32, data: &[u8]) -> Self {
        assert!(data.len() <= 8, "CAN data cannot exceed 8 bytes");

        let mut frame_data = [0u8; 8];
        frame_data[..data.len()].copy_from_slice(data);

        Self {
            id,
            dlc: data.len() as u8,
            data: frame_data,
            extended: id > 0x7FF,
        }
    }

    /// Returns the data portion of the frame (up to dlc bytes).
    pub fn data(&self) -> &[u8] {
        &self.data[..self.dlc as usize]
    }

    /// Formats the frame data as a hex string for logging.
    pub fn data_as_hex(&self) -> String {
        self.data()
            .iter()
            .map(|b| format!("{:02X}", b))
            .collect::<Vec<_>>()
            .join(" ")
    }
}

impl fmt::Debug for CanFrame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "CanFrame {{ id: 0x{:03X}, dlc: {}, data: [{}] }}",
            self.id,
            self.dlc,
            self.data_as_hex()
        )
    }
}

impl fmt::Display for CanFrame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:03X}#{}", self.id, self.data_as_hex().replace(' ', ""))
    }
}

/// Error types for CAN interface operations.
#[derive(Debug)]
pub enum CanError {
    /// Interface not found or not available.
    InterfaceNotFound(String),
    /// Failed to open the CAN socket.
    OpenFailed(io::Error),
    /// Failed to send a CAN frame.
    SendFailed(io::Error),
    /// Failed to receive a CAN frame.
    ReceiveFailed(io::Error),
    /// Receive operation timed out.
    Timeout,
    /// CAN bus is in bus-off state (too many errors).
    BusOff,
    /// Interface is not available on this platform.
    #[allow(dead_code)]
    NotSupported,
}

impl fmt::Display for CanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CanError::InterfaceNotFound(name) => {
                write!(f, "CAN interface '{}' not found", name)
            }
            CanError::OpenFailed(e) => write!(f, "Failed to open CAN socket: {}", e),
            CanError::SendFailed(e) => write!(f, "Failed to send CAN frame: {}", e),
            CanError::ReceiveFailed(e) => write!(f, "Failed to receive CAN frame: {}", e),
            CanError::Timeout => write!(f, "CAN receive timed out"),
            CanError::BusOff => write!(f, "CAN bus is in bus-off state"),
            CanError::NotSupported => write!(f, "CAN interface not supported on this platform"),
        }
    }
}

impl std::error::Error for CanError {}

/// Result type for CAN operations.
pub type CanResult<T> = Result<T, CanError>;

// ============================================================================
// Linux implementation using SocketCAN
// ============================================================================

#[cfg(target_os = "linux")]
mod linux_impl {
    use super::*;
    use socketcan::{CanSocket, Socket};
    use std::os::unix::io::AsRawFd;

    /// CAN interface wrapper for SocketCAN.
    ///
    /// Provides blocking send/receive operations with configurable timeouts.
    pub struct CanInterface {
        socket: CanSocket,
        interface_name: String,
        read_timeout: Option<Duration>,
    }

    impl CanInterface {
        /// Opens a CAN interface by name (e.g., "can0", "vcan0").
        ///
        /// # Arguments
        ///
        /// * `interface_name` - Name of the CAN interface
        ///
        /// # Errors
        ///
        /// Returns an error if the interface doesn't exist or can't be opened.
        pub fn open(interface_name: &str) -> CanResult<Self> {
            let socket = CanSocket::open(interface_name).map_err(|e| {
                if e.to_string().contains("No such device") {
                    CanError::InterfaceNotFound(interface_name.to_string())
                } else {
                    CanError::OpenFailed(io::Error::new(io::ErrorKind::Other, e.to_string()))
                }
            })?;

            Ok(Self {
                socket,
                interface_name: interface_name.to_string(),
                read_timeout: None,
            })
        }

        /// Sets the read timeout for receive operations.
        ///
        /// # Arguments
        ///
        /// * `timeout` - Timeout duration, or None for blocking forever
        pub fn set_read_timeout(&mut self, timeout: Option<Duration>) -> CanResult<()> {
            self.read_timeout = timeout;

            // Set socket timeout using setsockopt
            let fd = self.socket.as_raw_fd();
            let timeval = match timeout {
                Some(t) => libc::timeval {
                    tv_sec: t.as_secs() as libc::time_t,
                    tv_usec: t.subsec_micros() as libc::suseconds_t,
                },
                None => libc::timeval {
                    tv_sec: 0,
                    tv_usec: 0,
                },
            };

            let result = unsafe {
                libc::setsockopt(
                    fd,
                    libc::SOL_SOCKET,
                    libc::SO_RCVTIMEO,
                    &timeval as *const _ as *const libc::c_void,
                    std::mem::size_of::<libc::timeval>() as libc::socklen_t,
                )
            };

            if result < 0 {
                return Err(CanError::OpenFailed(io::Error::last_os_error()));
            }

            Ok(())
        }

        /// Sends a CAN frame.
        ///
        /// # Arguments
        ///
        /// * `frame` - The CAN frame to send
        ///
        /// # Errors
        ///
        /// Returns an error if the send fails.
        pub fn send(&self, frame: &CanFrame) -> CanResult<()> {
            use socketcan::frame::CanDataFrame;
            use socketcan::Id;

            let id = if frame.extended {
                Id::Extended(socketcan::ExtendedId::new(frame.id).unwrap())
            } else {
                Id::Standard(socketcan::StandardId::new(frame.id as u16).unwrap())
            };

            let can_frame = CanDataFrame::new(id, frame.data()).unwrap();

            self.socket
                .write_frame(&can_frame)
                .map_err(|e| CanError::SendFailed(io::Error::new(io::ErrorKind::Other, e)))?;

            Ok(())
        }

        /// Receives a CAN frame with timeout.
        ///
        /// # Errors
        ///
        /// Returns `CanError::Timeout` if no frame is received within the timeout.
        pub fn recv(&self) -> CanResult<CanFrame> {
            use socketcan::frame::Frame;

            match self.socket.read_frame() {
                Ok(frame) => {
                    let id = match frame.id() {
                        socketcan::Id::Standard(id) => id.as_raw() as u32,
                        socketcan::Id::Extended(id) => id.as_raw(),
                    };

                    let data = frame.data();
                    let mut frame_data = [0u8; 8];
                    let dlc = data.len().min(8);
                    frame_data[..dlc].copy_from_slice(&data[..dlc]);

                    Ok(CanFrame {
                        id,
                        dlc: dlc as u8,
                        data: frame_data,
                        extended: matches!(frame.id(), socketcan::Id::Extended(_)),
                    })
                }
                Err(e) => {
                    let err = e.to_string();
                    if err.contains("timed out") || err.contains("Resource temporarily unavailable")
                    {
                        Err(CanError::Timeout)
                    } else {
                        Err(CanError::ReceiveFailed(io::Error::new(
                            io::ErrorKind::Other,
                            err,
                        )))
                    }
                }
            }
        }

        /// Returns the interface name.
        pub fn name(&self) -> &str {
            &self.interface_name
        }
    }
}

// ============================================================================
// Stub implementation for non-Linux platforms (for compilation only)
// ============================================================================

#[cfg(not(target_os = "linux"))]
mod stub_impl {
    use super::*;

    /// Stub CAN interface for non-Linux platforms.
    ///
    /// This implementation allows the code to compile on Windows/macOS,
    /// but all operations return `NotSupported` errors.
    pub struct CanInterface {
        interface_name: String,
        #[allow(dead_code)]
        read_timeout: Option<Duration>,
    }

    impl CanInterface {
        pub fn open(interface_name: &str) -> CanResult<Self> {
            log::warn!(
                "SocketCAN is not available on this platform. \
                 CAN operations will not work."
            );
            Ok(Self {
                interface_name: interface_name.to_string(),
                read_timeout: None,
            })
        }

        pub fn set_read_timeout(&mut self, timeout: Option<Duration>) -> CanResult<()> {
            self.read_timeout = timeout;
            Ok(())
        }

        pub fn send(&self, _frame: &CanFrame) -> CanResult<()> {
            Err(CanError::NotSupported)
        }

        pub fn recv(&self) -> CanResult<CanFrame> {
            Err(CanError::NotSupported)
        }

        pub fn name(&self) -> &str {
            &self.interface_name
        }
    }
}

// Re-export the appropriate implementation
#[cfg(target_os = "linux")]
pub use linux_impl::CanInterface;
#[cfg(not(target_os = "linux"))]
pub use stub_impl::CanInterface;

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_can_frame_new() {
        let frame = CanFrame::new(0x7DF, &[0x02, 0x01, 0x0C]);
        assert_eq!(frame.id, 0x7DF);
        assert_eq!(frame.dlc, 3);
        assert_eq!(&frame.data[..3], &[0x02, 0x01, 0x0C]);
        assert!(!frame.extended);
    }

    #[test]
    fn test_can_frame_extended() {
        let frame = CanFrame::new(0x18DAF110, &[0x02, 0x01, 0x00]);
        assert!(frame.extended);
    }

    #[test]
    fn test_can_frame_data_as_hex() {
        let frame = CanFrame::new(0x7E8, &[0x04, 0x41, 0x0C, 0x2E, 0xE0]);
        assert_eq!(frame.data_as_hex(), "04 41 0C 2E E0");
    }

    #[test]
    fn test_can_frame_display() {
        let frame = CanFrame::new(0x7E8, &[0x04, 0x41, 0x0C, 0x2E, 0xE0]);
        assert_eq!(format!("{}", frame), "7E8#04410C2EE0");
    }

    #[test]
    #[should_panic(expected = "CAN data cannot exceed 8 bytes")]
    fn test_can_frame_too_long() {
        let _ = CanFrame::new(0x7DF, &[0; 9]);
    }
}
