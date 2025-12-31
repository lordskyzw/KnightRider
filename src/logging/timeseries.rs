//! Timeseries logging to CSV files.
//!
//! Handles writing CAN frames and decoded values to CSV files with rotation.

use std::fs::{File, OpenOptions};
use std::io::{self, BufWriter, Write};
use std::path::PathBuf;
use chrono::{DateTime, Utc};

/// Log entry for raw CAN frames.
#[derive(Debug, Clone)]
pub struct RawFrameEntry {
    pub timestamp: DateTime<Utc>,
    pub can_id: u32,
    pub dlc: u8,
    pub data: [u8; 8],
}

impl RawFrameEntry {
    /// Formats as CSV line.
    pub fn to_csv(&self) -> String {
        let ts = self.timestamp.format("%Y-%m-%dT%H:%M:%S%.3fZ");
        let data_hex = self.data[..self.dlc as usize]
            .iter()
            .map(|b| format!("{:02X}", b))
            .collect::<Vec<_>>()
            .join(" ");
        format!("{},0x{:03X},{},{}", ts, self.can_id, self.dlc, data_hex)
    }
}

/// Logger for raw CAN frames.
pub struct TimeseriesLogger {
    writer: Option<BufWriter<File>>,
    path: PathBuf,
    entries_written: usize,
}

impl TimeseriesLogger {
    /// Creates a new logger writing to the specified path.
    pub fn new(path: PathBuf) -> io::Result<Self> {
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&path)?;
        
        let mut writer = BufWriter::new(file);
        writeln!(writer, "timestamp,can_id,dlc,data")?;
        
        Ok(Self {
            writer: Some(writer),
            path,
            entries_written: 0,
        })
    }

    /// Logs a raw CAN frame.
    pub fn log_frame(&mut self, entry: &RawFrameEntry) -> io::Result<()> {
        if let Some(ref mut writer) = self.writer {
            writeln!(writer, "{}", entry.to_csv())?;
            self.entries_written += 1;
            
            // Flush every 100 entries
            if self.entries_written % 100 == 0 {
                writer.flush()?;
            }
        }
        Ok(())
    }

    /// Flushes any buffered data to disk.
    pub fn flush(&mut self) -> io::Result<()> {
        if let Some(ref mut writer) = self.writer {
            writer.flush()?;
        }
        Ok(())
    }

    /// Returns the log file path.
    pub fn path(&self) -> &PathBuf {
        &self.path
    }
}

impl Drop for TimeseriesLogger {
    fn drop(&mut self) {
        let _ = self.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_raw_frame_csv() {
        let entry = RawFrameEntry {
            timestamp: DateTime::parse_from_rfc3339("2025-12-31T14:23:45.123Z")
                .unwrap()
                .with_timezone(&Utc),
            can_id: 0x7E8,
            dlc: 5,
            data: [0x04, 0x41, 0x0C, 0x2E, 0xE0, 0x00, 0x00, 0x00],
        };
        
        let csv = entry.to_csv();
        assert!(csv.contains("2025-12-31T14:23:45.123Z"));
        assert!(csv.contains("0x7E8"));
        assert!(csv.contains("04 41 0C 2E E0"));
    }
}
