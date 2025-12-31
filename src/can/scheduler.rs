//! Request timing and rate limiting.
//!
//! Handles OBD-II request scheduling to respect ECU timing constraints:
//! - Minimum 100ms between requests to same ECU
//! - 200ms timeout for responses

use std::time::{Duration, Instant};

/// Default timeout for OBD-II responses.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_millis(200);

/// Minimum gap between requests to the same ECU.
pub const MIN_REQUEST_GAP: Duration = Duration::from_millis(100);

/// Request scheduler for OBD-II polling.
#[derive(Debug)]
pub struct RequestScheduler {
    last_request: Option<Instant>,
    request_interval: Duration,
    response_timeout: Duration,
}

impl Default for RequestScheduler {
    fn default() -> Self {
        Self::new(MIN_REQUEST_GAP, DEFAULT_TIMEOUT)
    }
}

impl RequestScheduler {
    /// Creates a new scheduler with specified intervals.
    pub fn new(request_interval: Duration, response_timeout: Duration) -> Self {
        Self {
            last_request: None,
            request_interval,
            response_timeout,
        }
    }

    /// Checks if we can send a new request now.
    pub fn can_send(&self) -> bool {
        match self.last_request {
            None => true,
            Some(last) => last.elapsed() >= self.request_interval,
        }
    }

    /// Returns how long to wait before the next request can be sent.
    pub fn time_until_next(&self) -> Duration {
        match self.last_request {
            None => Duration::ZERO,
            Some(last) => {
                let elapsed = last.elapsed();
                if elapsed >= self.request_interval {
                    Duration::ZERO
                } else {
                    self.request_interval - elapsed
                }
            }
        }
    }

    /// Marks that a request was just sent.
    pub fn mark_sent(&mut self) {
        self.last_request = Some(Instant::now());
    }

    /// Returns the response timeout duration.
    pub fn timeout(&self) -> Duration {
        self.response_timeout
    }

    /// Checks if a request has timed out.
    pub fn is_timed_out(&self) -> bool {
        match self.last_request {
            None => false,
            Some(last) => last.elapsed() >= self.response_timeout,
        }
    }

    /// Waits until we can send the next request.
    pub fn wait_for_next(&self) {
        let wait_time = self.time_until_next();
        if !wait_time.is_zero() {
            std::thread::sleep(wait_time);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scheduler_initial() {
        let scheduler = RequestScheduler::default();
        assert!(scheduler.can_send());
        assert_eq!(scheduler.time_until_next(), Duration::ZERO);
    }

    #[test]
    fn test_scheduler_after_send() {
        let mut scheduler = RequestScheduler::default();
        scheduler.mark_sent();
        assert!(!scheduler.can_send());
        assert!(scheduler.time_until_next() > Duration::ZERO);
    }
}
