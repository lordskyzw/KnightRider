//! SQLite-backed canonical buffer.
//!
//! WAL mode, monotonic `batch_id` (read max+1 on open so it survives reboots),
//! append + range-query API.

// Filled in by task #6.
