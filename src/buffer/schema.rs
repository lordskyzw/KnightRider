//! Versioned batch envelope + payload formats.
//!
//! Wire-stable on-disk format that the cloud ingest also accepts. Versioning
//! is split into two integers:
//!
//! - [`ENVELOPE_SCHEMA_V`] controls the outer envelope (this struct).
//! - [`SAMPLES_PAYLOAD_SCHEMA_V`] controls the inner samples payload.
//!
//! Bump the matching constant on a backwards-incompatible change; minor
//! additive changes (new optional fields with `serde(default)`) do not.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::extractor::Sample;

/// Envelope schema version. Bump on backwards-incompatible envelope changes.
pub const ENVELOPE_SCHEMA_V: u32 = 1;

/// Samples payload schema version. Bump on backwards-incompatible sample
/// format changes.
pub const SAMPLES_PAYLOAD_SCHEMA_V: u32 = 1;

/// On-disk and on-the-wire format for a Pi-produced batch of samples.
///
/// The cloud ingest verifies `envelope_schema_v` is supported, decodes the
/// payload according to `payload_schema_v`, and (once signing is enabled in a
/// follow-up) verifies the signature against the device's registered public
/// key.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchEnvelope {
    pub envelope_schema_v: u32,
    pub payload_schema_v: u32,
    pub device_id: Uuid,
    pub batch_id: u64,
    pub created_at: DateTime<Utc>,
    pub sample_count: u32,
    /// CBOR-encoded [`SamplesPayload`].
    pub payload: Vec<u8>,
    /// ed25519 signature over the canonical pre-image of this envelope.
    /// `None` during the demo phase; required once signing lands.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub signature: Option<Vec<u8>>,
}

/// Decoded payload body for `payload_schema_v == 1`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SamplesPayload {
    pub samples: Vec<Sample>,
}

#[derive(Debug, thiserror::Error)]
pub enum SchemaError {
    #[error("unsupported envelope schema version: {0}")]
    UnsupportedEnvelopeVersion(u32),
    #[error("unsupported payload schema version: {0}")]
    UnsupportedPayloadVersion(u32),
    #[error("CBOR codec error: {0}")]
    Cbor(String),
}

impl BatchEnvelope {
    /// Builds an envelope from a freshly-sealed batch of samples.
    pub fn build(
        device_id: Uuid,
        batch_id: u64,
        samples: Vec<Sample>,
    ) -> Result<Self, SchemaError> {
        let sample_count = samples.len() as u32;
        let payload_obj = SamplesPayload { samples };
        let mut payload = Vec::new();
        ciborium::into_writer(&payload_obj, &mut payload)
            .map_err(|e| SchemaError::Cbor(e.to_string()))?;

        Ok(Self {
            envelope_schema_v: ENVELOPE_SCHEMA_V,
            payload_schema_v: SAMPLES_PAYLOAD_SCHEMA_V,
            device_id,
            batch_id,
            created_at: Utc::now(),
            sample_count,
            payload,
            signature: None,
        })
    }

    pub fn decode_payload(&self) -> Result<SamplesPayload, SchemaError> {
        if self.payload_schema_v != SAMPLES_PAYLOAD_SCHEMA_V {
            return Err(SchemaError::UnsupportedPayloadVersion(self.payload_schema_v));
        }
        ciborium::from_reader(self.payload.as_slice())
            .map_err(|e| SchemaError::Cbor(e.to_string()))
    }

    /// CBOR-encodes the envelope itself. Used for SQLite storage and wire.
    pub fn encode(&self) -> Result<Vec<u8>, SchemaError> {
        let mut buf = Vec::new();
        ciborium::into_writer(self, &mut buf).map_err(|e| SchemaError::Cbor(e.to_string()))?;
        Ok(buf)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, SchemaError> {
        let envelope: BatchEnvelope =
            ciborium::from_reader(bytes).map_err(|e| SchemaError::Cbor(e.to_string()))?;
        if envelope.envelope_schema_v != ENVELOPE_SCHEMA_V {
            return Err(SchemaError::UnsupportedEnvelopeVersion(
                envelope.envelope_schema_v,
            ));
        }
        Ok(envelope)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extractor::sample::SampleSource;

    #[test]
    fn roundtrip_envelope_through_cbor() {
        let device = Uuid::nil();
        let samples = vec![
            Sample::new(SampleSource::ObdPoller, "obd.rpm", 3000.0, "rpm"),
            Sample::new(SampleSource::ObdPoller, "obd.speed", 60.0, "km/h"),
        ];
        let env = BatchEnvelope::build(device, 42, samples).expect("build");
        assert_eq!(env.sample_count, 2);
        assert_eq!(env.envelope_schema_v, ENVELOPE_SCHEMA_V);
        assert_eq!(env.payload_schema_v, SAMPLES_PAYLOAD_SCHEMA_V);

        let encoded = env.encode().expect("encode");
        let decoded = BatchEnvelope::decode(&encoded).expect("decode");
        assert_eq!(decoded.batch_id, 42);
        assert_eq!(decoded.sample_count, 2);

        let payload = decoded.decode_payload().expect("decode payload");
        assert_eq!(payload.samples.len(), 2);
        assert_eq!(payload.samples[0].signal, "obd.rpm");
        assert_eq!(payload.samples[0].value, 3000.0);
        assert_eq!(payload.samples[1].signal, "obd.speed");
    }

    #[test]
    fn rejects_future_envelope_version() {
        let mut env = BatchEnvelope::build(Uuid::nil(), 1, vec![]).unwrap();
        env.envelope_schema_v = 999;
        let bytes = env.encode().unwrap();
        let err = BatchEnvelope::decode(&bytes).unwrap_err();
        matches!(err, SchemaError::UnsupportedEnvelopeVersion(999));
    }
}
