//! Passive CAN sniffer (DBC-driven decode).
//!
//! Reads broadcast CAN frames and decodes them via a loaded opendbc-style
//! profile, emitting [`Sample`]s at the bus rate (typically 50-100 Hz for the
//! frames a vehicle's instrument cluster reads from).
//!
//! Status: **skeleton**. The thread is real and exits cleanly, but no opendbc
//! integration has shipped yet. Until a DBC parser is wired in, the sniffer
//! produces no samples. The OBD poller carries the live dashboard in the
//! meantime.

use std::thread::JoinHandle;
use std::time::Duration;

use crate::can::CanInterface;
use crate::extractor::SampleSender;

/// Spawns the sniffer on a dedicated thread.
///
/// Currently a no-op: logs a warning and exits. Kept callable from `main` so
/// the rest of the pipeline stays wired while opendbc integration lands.
pub fn spawn(_can: CanInterface, _tx: SampleSender) -> JoinHandle<()> {
    std::thread::Builder::new()
        .name("can-sniffer".into())
        .spawn(|| {
            log::info!(
                "can-sniffer: opendbc decode not yet implemented; sniffer is a no-op for now"
            );
            // Keep the thread alive briefly so log output ordering is sane.
            std::thread::sleep(Duration::from_millis(50));
        })
        .expect("spawn can-sniffer thread")
}
