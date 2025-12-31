//! In-memory ring buffer for log entries.
//!
//! Provides a fixed-size buffer that overwrites oldest entries when full.

use std::collections::VecDeque;

/// Default buffer capacity (1MB worth of entries, ~30s at 100Hz).
const DEFAULT_CAPACITY: usize = 10000;

/// Ring buffer for storing log entries.
#[derive(Debug)]
pub struct RingBuffer<T> {
    buffer: VecDeque<T>,
    capacity: usize,
}

impl<T> Default for RingBuffer<T> {
    fn default() -> Self {
        Self::new(DEFAULT_CAPACITY)
    }
}

impl<T> RingBuffer<T> {
    /// Creates a new ring buffer with the specified capacity.
    pub fn new(capacity: usize) -> Self {
        Self {
            buffer: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    /// Appends an entry to the buffer, removing the oldest if full.
    pub fn push(&mut self, entry: T) {
        if self.buffer.len() >= self.capacity {
            self.buffer.pop_front();
        }
        self.buffer.push_back(entry);
    }

    /// Returns the number of entries in the buffer.
    pub fn len(&self) -> usize {
        self.buffer.len()
    }

    /// Returns true if the buffer is empty.
    pub fn is_empty(&self) -> bool {
        self.buffer.is_empty()
    }

    /// Drains all entries from the buffer.
    pub fn drain(&mut self) -> impl Iterator<Item = T> + '_ {
        self.buffer.drain(..)
    }

    /// Returns an iterator over the entries.
    pub fn iter(&self) -> impl Iterator<Item = &T> {
        self.buffer.iter()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ring_buffer() {
        let mut buffer = RingBuffer::new(3);
        buffer.push(1);
        buffer.push(2);
        buffer.push(3);
        assert_eq!(buffer.len(), 3);
        
        buffer.push(4);
        assert_eq!(buffer.len(), 3);
        
        let entries: Vec<_> = buffer.drain().collect();
        assert_eq!(entries, vec![2, 3, 4]);
    }
}
