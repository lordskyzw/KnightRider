//! SQLite-backed canonical buffer.
//!
//! WAL mode, monotonic `batch_id` (read `MAX(batch_id) + 1` on open so it
//! survives reboots), append + range-query API. Blocking — callers must use
//! [`tokio::task::spawn_blocking`] from async contexts.

use std::path::Path;

use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};
use uuid::Uuid;

use crate::buffer::schema::{BatchEnvelope, SchemaError};
use crate::extractor::Sample;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("schema error: {0}")]
    Schema(#[from] SchemaError),
    #[error("uuid parse error: {0}")]
    UuidParse(String),
}

pub type StoreResult<T> = Result<T, StoreError>;

/// Canonical SQLite buffer of Pi-produced batches.
pub struct Store {
    conn: Connection,
    device_id: Uuid,
    next_batch_id: u64,
}

impl Store {
    /// Opens (or creates) the buffer at `path`. Initializes WAL, schema, and
    /// device_id on first use; otherwise picks up where it left off.
    pub fn open(path: impl AsRef<Path>) -> StoreResult<Self> {
        let conn = Connection::open(path)?;
        Self::init(conn)
    }

    /// In-memory store, intended for tests.
    pub fn open_in_memory() -> StoreResult<Self> {
        let conn = Connection::open_in_memory()?;
        Self::init(conn)
    }

    fn init(conn: Connection) -> StoreResult<Self> {
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA temp_store = MEMORY;
             CREATE TABLE IF NOT EXISTS meta (
                 key   TEXT PRIMARY KEY,
                 value TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS batches (
                 batch_id     INTEGER PRIMARY KEY,
                 created_at   TEXT NOT NULL,
                 sample_count INTEGER NOT NULL,
                 envelope     BLOB NOT NULL,
                 uploaded_at  TEXT
             );
             CREATE INDEX IF NOT EXISTS idx_batches_pending
                 ON batches(batch_id) WHERE uploaded_at IS NULL;",
        )?;

        let device_id = Self::load_or_create_device_id(&conn)?;
        let next_batch_id = Self::load_next_batch_id(&conn)?;

        Ok(Self { conn, device_id, next_batch_id })
    }

    fn load_or_create_device_id(conn: &Connection) -> StoreResult<Uuid> {
        let existing: Option<String> = conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'device_id'",
                [],
                |r| r.get(0),
            )
            .optional()?;

        if let Some(s) = existing {
            return Uuid::parse_str(&s).map_err(|e| StoreError::UuidParse(e.to_string()));
        }

        let new_id = Uuid::new_v4();
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('device_id', ?1)",
            params![new_id.to_string()],
        )?;
        Ok(new_id)
    }

    fn load_next_batch_id(conn: &Connection) -> StoreResult<u64> {
        let max: Option<i64> = conn
            .query_row("SELECT MAX(batch_id) FROM batches", [], |r| r.get(0))
            .optional()?
            .flatten();
        Ok(max.map(|v| v as u64 + 1).unwrap_or(1))
    }

    pub fn device_id(&self) -> Uuid {
        self.device_id
    }

    pub fn next_batch_id(&self) -> u64 {
        self.next_batch_id
    }

    /// Seals `samples` into a [`BatchEnvelope`] and persists it. Returns the
    /// assigned `batch_id`. A no-op (returns 0) when `samples` is empty.
    pub fn append(&mut self, samples: Vec<Sample>) -> StoreResult<u64> {
        if samples.is_empty() {
            return Ok(0);
        }

        let batch_id = self.next_batch_id;
        let envelope = BatchEnvelope::build(self.device_id, batch_id, samples)?;
        let encoded = envelope.encode()?;

        self.conn.execute(
            "INSERT INTO batches (batch_id, created_at, sample_count, envelope)
             VALUES (?1, ?2, ?3, ?4)",
            params![
                batch_id as i64,
                envelope.created_at.to_rfc3339(),
                envelope.sample_count as i64,
                encoded,
            ],
        )?;

        self.next_batch_id = self.next_batch_id.wrapping_add(1);
        Ok(batch_id)
    }

    /// Returns batches with `batch_id > since`, ordered ascending, capped at
    /// `limit`. Used by the `/backlog?since=N` server endpoint.
    pub fn query_since(&self, since: u64, limit: usize) -> StoreResult<Vec<BatchEnvelope>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT envelope FROM batches
             WHERE batch_id > ?1
             ORDER BY batch_id ASC
             LIMIT ?2",
        )?;

        let rows = stmt.query_map(params![since as i64, limit as i64], |r| {
            let bytes: Vec<u8> = r.get(0)?;
            Ok(bytes)
        })?;

        let mut out = Vec::new();
        for row in rows {
            let bytes = row?;
            out.push(BatchEnvelope::decode(&bytes)?);
        }
        Ok(out)
    }

    /// Marks a batch as confirmed-uploaded by a courier.
    pub fn mark_uploaded(&self, batch_id: u64, when: DateTime<Utc>) -> StoreResult<()> {
        self.conn.execute(
            "UPDATE batches SET uploaded_at = ?1 WHERE batch_id = ?2",
            params![when.to_rfc3339(), batch_id as i64],
        )?;
        Ok(())
    }

    /// Counts batches still waiting on courier upload.
    pub fn pending_count(&self) -> StoreResult<u64> {
        let n: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM batches WHERE uploaded_at IS NULL",
            [],
            |r| r.get(0),
        )?;
        Ok(n as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extractor::sample::SampleSource;

    fn rpm(value: f64) -> Sample {
        Sample::new(SampleSource::ObdPoller, "obd.rpm", value, "rpm")
    }

    #[test]
    fn round_trip_append_and_query() {
        let mut store = Store::open_in_memory().expect("open");
        let id1 = store.append(vec![rpm(1000.0), rpm(2000.0)]).unwrap();
        let id2 = store.append(vec![rpm(3000.0)]).unwrap();
        assert_eq!(id1, 1);
        assert_eq!(id2, 2);

        let all = store.query_since(0, 100).unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].batch_id, 1);
        assert_eq!(all[0].sample_count, 2);
        assert_eq!(all[1].batch_id, 2);
        assert_eq!(all[1].sample_count, 1);

        let tail = store.query_since(1, 100).unwrap();
        assert_eq!(tail.len(), 1);
        assert_eq!(tail[0].batch_id, 2);
    }

    #[test]
    fn empty_append_is_noop() {
        let mut store = Store::open_in_memory().unwrap();
        let id = store.append(vec![]).unwrap();
        assert_eq!(id, 0);
        assert_eq!(store.next_batch_id(), 1);
    }

    #[test]
    fn pending_count_reflects_uploads() {
        let mut store = Store::open_in_memory().unwrap();
        let id1 = store.append(vec![rpm(100.0)]).unwrap();
        let _ = store.append(vec![rpm(200.0)]).unwrap();
        assert_eq!(store.pending_count().unwrap(), 2);
        store.mark_uploaded(id1, Utc::now()).unwrap();
        assert_eq!(store.pending_count().unwrap(), 1);
    }

    #[test]
    fn device_id_persists_across_reopen() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = tmp.path().to_path_buf();
        drop(tmp); // we just want the path; let Store create the file.

        let id1 = {
            let s = Store::open(&path).unwrap();
            s.device_id()
        };
        let id2 = {
            let s = Store::open(&path).unwrap();
            s.device_id()
        };
        assert_eq!(id1, id2);

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn batch_id_monotonic_across_reopen() {
        let tmp = tempfile::NamedTempFile::new().unwrap();
        let path = tmp.path().to_path_buf();
        drop(tmp);

        {
            let mut s = Store::open(&path).unwrap();
            assert_eq!(s.append(vec![rpm(1.0)]).unwrap(), 1);
            assert_eq!(s.append(vec![rpm(2.0)]).unwrap(), 2);
        }
        {
            let mut s = Store::open(&path).unwrap();
            assert_eq!(s.next_batch_id(), 3);
            assert_eq!(s.append(vec![rpm(3.0)]).unwrap(), 3);
        }

        let _ = std::fs::remove_file(&path);
    }
}
