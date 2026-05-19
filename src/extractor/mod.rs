//! Signal extraction layer.
//!
//! Two parallel extractors feed a single broadcast channel of [`Sample`]s:
//!
//! - [`obd_poller`]: ISO 15031 request/response over CAN. Universal across cars,
//!   ~10-25 Hz per signal. Linux-only at the I/O layer.
//! - [`sniffer`]: passive CAN frame capture decoded against an opendbc-style DBC.
//!   50-100 Hz when a profile is loaded for the target car. Linux-only at the
//!   I/O layer; opendbc decode integration ships in a follow-up.
//!
//! Downstream consumers (buffer writer, WebSocket broadcast, known-track inference)
//! subscribe to the broadcast channel. Slow subscribers experience `Lagged`
//! errors and skip samples rather than blocking publishers — the canonical
//! buffer is the durable store; live consumers are best-effort.

pub mod obd_poller;
pub mod sample;
pub mod sniffer;

pub use sample::{Sample, SampleSource};

pub type SampleSender = tokio::sync::broadcast::Sender<Sample>;
pub type SampleReceiver = tokio::sync::broadcast::Receiver<Sample>;

/// Default broadcast capacity. Sized to absorb ~5s of bursty 50 Hz sniffer
/// traffic without overflowing slow subscribers.
pub const DEFAULT_CHANNEL_CAPACITY: usize = 256;

/// Construct the default sample broadcast channel.
pub fn channel() -> (SampleSender, SampleReceiver) {
    tokio::sync::broadcast::channel(DEFAULT_CHANNEL_CAPACITY)
}
