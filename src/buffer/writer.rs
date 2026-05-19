//! Buffer-writer task: drains the extractor broadcast into the canonical
//! [`Store`].
//!
//! Flushes when either a count threshold is reached or a time interval
//! elapses, whichever comes first. SQLite work is done on
//! [`tokio::task::spawn_blocking`] so the runtime is never blocked on disk
//! I/O.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::sync::broadcast::error::RecvError;
use tokio::time::{interval, MissedTickBehavior};

use crate::buffer::store::Store;
use crate::extractor::{Sample, SampleReceiver};

#[derive(Debug, Clone)]
pub struct WriterConfig {
    /// Maximum time a sample sits in the in-memory buffer before flush.
    pub flush_every: Duration,
    /// Maximum samples in the in-memory buffer before flush.
    pub flush_at: usize,
}

impl Default for WriterConfig {
    fn default() -> Self {
        Self {
            flush_every: Duration::from_secs(5),
            flush_at: 500,
        }
    }
}

/// Runs the writer loop until the broadcast channel closes (i.e. the last
/// sender drops). Flushes any pending buffer on exit.
pub async fn run(store: Arc<Mutex<Store>>, mut rx: SampleReceiver, config: WriterConfig) {
    let mut buf: Vec<Sample> = Vec::with_capacity(config.flush_at);
    let mut tick = interval(config.flush_every);
    tick.set_missed_tick_behavior(MissedTickBehavior::Delay);
    tick.tick().await; // skip the first immediate tick

    log::info!(
        "buffer-writer: flush every {:?} or every {} samples",
        config.flush_every, config.flush_at,
    );

    loop {
        tokio::select! {
            sample = rx.recv() => match sample {
                Ok(s) => {
                    buf.push(s);
                    if buf.len() >= config.flush_at {
                        flush(&store, &mut buf).await;
                    }
                }
                Err(RecvError::Lagged(n)) => {
                    log::warn!("buffer-writer: lagged {} samples", n);
                }
                Err(RecvError::Closed) => {
                    flush(&store, &mut buf).await;
                    break;
                }
            },
            _ = tick.tick() => {
                if !buf.is_empty() {
                    flush(&store, &mut buf).await;
                }
            }
        }
    }

    log::info!("buffer-writer: stopped");
}

async fn flush(store: &Arc<Mutex<Store>>, buf: &mut Vec<Sample>) {
    if buf.is_empty() {
        return;
    }
    let samples = std::mem::take(buf);
    let n = samples.len();
    let store = store.clone();
    match tokio::task::spawn_blocking(move || {
        let mut guard = store.lock().expect("store mutex poisoned");
        guard.append(samples)
    })
    .await
    {
        Ok(Ok(batch_id)) => log::debug!("buffer-writer: flushed batch {} ({} samples)", batch_id, n),
        Ok(Err(e)) => log::warn!("buffer-writer: append failed: {}", e),
        Err(e) => log::warn!("buffer-writer: blocking task panicked: {}", e),
    }
}
