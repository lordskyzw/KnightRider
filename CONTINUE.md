# Knight Rider — Session Resume Doc

**Read me first if you're a future Claude session resuming this work.**

This file is the live handoff between sessions. The auto-loaded memory at
`.claude/projects/C--Users-Hp-830-G5-KnightRider/memory/` has the locked-in
architecture; this file has the live backlog and the last-shipped state.
**Git is authoritative** for what's actually in the code — memory describes
intent, this file describes the plan, only git describes reality.

---

## What this project is (one paragraph)

In-car telemetry + unsupervised fault detection. **Pi** (Rust binary
`knight-rider`) reads CAN/OBD-II, buffers Pi-signed batches in SQLite, and
serves a local LAN via HTTP+WS. **Flutter app** (`mobile/`) connects on LAN
to show a live dashboard and acts as a **courier** that carries batches to
the **cloud** (Railway, not built yet) when off-LAN. Cloud runs the
**discovery track** (RA-Typed GP) against accumulating data — that's the
research-publication path. A **known track** runs on the Pi for instant
local alerts. The discovery→known-track promotion loop closes via cloud-built
signed bundles delivered back to the Pi `/inbox` by courier phones.

Demo phase = no auth on LAN, no ed25519 yet, no OTA. Single Pi, single car.

---

## Resume protocol

When you start a session:

1. **Read this file.**
2. `git log --oneline -15` — see what's actually shipped.
3. The auto-loaded memory in `.claude/projects/.../memory/MEMORY.md` already
   gives you the architecture. Don't re-derive it; don't ask the user to
   re-explain.
4. Pick the next item from **Up next** below and propose it (don't just dive
   in if it's non-trivial — confirm with the user first).

When you finish a chunk of work, before stopping:

1. Update this file: move the chunk from "Up next" to "Shipped" with the
   commit SHA. Refine the remaining backlog if your work changed it.
2. Commit `CONTINUE.md` with a `docs: update CONTINUE.md` commit (or fold
   into your feature commit).

---

## Shipped so far

This repo (`KnightRider`):

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| `20cf44f`| Extractor scaffolding: Sample broadcast channel, obd_poller migrated off main.rs, sniffer stub. |
| `9e8a16d`| Versioned `BatchEnvelope` (CBOR, split envelope/payload versions) + SQLite WAL `Store` with monotonic batch_id that survives reboots. |
| `12edb30`| axum server on `0.0.0.0:8080`: `/health`, `/ws/live`, `/backlog?since=N` (NDJSON, envelope_b64 verbatim), `/known-track/alerts` + `/inbox` stubs. |
| `73d2b6b`| `main.rs` rewired to tokio service: CAN → broadcast → buffer-writer + server. Race-free subscribe order. ctrl-c flushes writer. |
| `cc7dbdb`| Flutter mobile under `mobile/`: dark dashboard (RPM big + 5 small gauges + backlog counts + status chip), `WsClient` w/ 2s auto-reconnect, sqflite backlog store, settings screen. |

Sibling repo `../knight-rider-cloud`:

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| `e9ffa16`| Cloud ingest API. FastAPI + SQLModel. `POST /v1/batches` accepts the Pi's `/backlog` JSON/NDJSON rows, idempotent on `(device_id, batch_id)`, stores raw envelope bytes verbatim. SQLite local + Postgres on Railway via `DATABASE_URL`. railway.toml + Procfile + README with deploy steps. 8 pytest tests pass. **Not yet deployed to Railway** — needs `railway login && railway init && railway add --plugin postgresql && railway up`. |

Test coverage: 8 Rust unit tests + 1 Flutter widget test + 8 cloud pytest tests, all passing.

---

## Up next (priority order)

### 1. Deploy cloud + Flutter courier upload path
Cloud exists locally but not on Railway. Once deployed, wire the Flutter app
to POST `BacklogDb` rows to `<railway-url>/v1/batches`.

Two halves:
- **Deploy**: `cd ../knight-rider-cloud && railway login && railway init && railway add --plugin postgresql && railway up`. Verify `<deploy>.up.railway.app/health` returns 200. Save the URL.
- **Wire courier**: new `mobile/lib/uploader.dart`. Background timer (or `connectivity_plus` listener) that, when off-LAN AND has internet, reads un-uploaded rows from `BacklogDb` and POSTs them to the cloud URL in chunks of ~100 rows. On 200 response, calls `BacklogDb.markUploaded(batchId, now)`. Cloud URL stored in `PiConfig`. Cloud's idempotency makes retries safe.

### 2. Discovery worker skeleton (cloud-side)
In `knight-rider-cloud`. New `src/knight_rider_cloud/worker/`. Reads recent
batches from Postgres, CBOR-decodes each envelope (Python `cbor2`), extracts
samples into a per-device timeseries, then runs the RA-Typed GP. For first
ship: just decode + write a `discovery_runs` row with a summary; actual GP
integration comes after we know the data shape in practice. Also add
`GET /v1/devices/:id/recent` so the worker (or you) can inspect what arrived.

### 3. opendbc sniffer (the demo wow-moment)
Replace the no-op in `src/extractor/sniffer.rs`. Options for DBC parsing:
- `can-dbc` crate (basic but works)
- vendor a parsed subset of opendbc as JSON tables (no runtime DBC parsing)
The sniffer needs its own `CanInterface` instance reading raw frames; the
poller's interface can't be shared because `recv()` is owned by it. Wire it
into `main.rs` after picking the DBC approach.

### 4. Known-track inference on the Pi
New module `src/known_track/`. Inputs: live `Sample` stream + a loaded
`KnownTrackBundle` (signed cloud artifact). Outputs: alerts to `/known-track/alerts`. For demo: DTC reading via OBD Mode 03 + a handful of threshold rules. The model-bundle loader (`src/known_track/bundle.rs`) with atomic swap + previous-bundle rollback is the harder half — but it's the piece the architecture loop hinges on.

### 5. ed25519 batch signing
Adds a `signature` field that's already reserved in `BatchEnvelope`. Pi
generates keypair on first boot, persists privkey in `meta` table, registers
pubkey with cloud on first contact (or out-of-band). Cloud verifies each
batch's signature against the registered key. **Don't ship this until
there's a real cloud to verify against** — premature otherwise.

### 6. Pi↔phone pairing
Deferred per user. Revisit when leaving demo phase. Likely flow: QR code
shown via a quick CLI helper on the Pi (since Pi has no screen), scanned by
Flutter, exchange a symmetric key cached on each side.

### 7. App / binary OTA
Deferred. Use `git pull && cargo build && systemctl restart` on the Pi for
now. Real OTA is ~1-2 weeks of plumbing (signed bundles, A/B partitions,
boot-time rollback) and isn't needed for demo.

---

## House rules (re-state every session — these are easy to drift from)

- **Don't ask the user to re-explain the architecture.** It's in memory.
- **Commit per logical chunk** — credits may run out mid-session; preserve work.
- **Demo phase**: open LAN, no signing, single car. Don't quietly add auth/signing/OTA.
- **Pi runs no internet code.** Sync is entirely the phone app's job.
- **Idempotency on cloud ingest is non-negotiable.**
- **Two extractors → one broadcast channel.** Don't fork the pipeline.
- **Phones store `envelope_b64` verbatim.** Don't decode-and-reencode mid-courier — the (future) Pi signature has to stay valid end-to-end.
- **CRLF warnings on git** are expected on Windows — ignore them.

---

## Quick verification

```bash
# Rust side
cargo test --lib                 # 8 tests should pass
cargo check --bins --tests       # no errors (warnings in obd-logger are pre-existing)

# Pi service
cargo run --bin knight-rider -- --interface vcan0 --bind 127.0.0.1:8088
curl http://127.0.0.1:8088/health
curl 'http://127.0.0.1:8088/backlog?since=0&limit=10'

# Flutter
cd mobile
flutter analyze                  # No issues
flutter test                     # 1 widget test passes

# Cloud (sibling repo)
cd ../knight-rider-cloud
python -m pytest -q              # 8 tests should pass
python -m uvicorn knight_rider_cloud.main:app --reload   # → http://127.0.0.1:8000/docs
```

## Known dev-env quirks

- **Spaces in `C:\Users\Hp 830 G5\` break Python 3.9 `venv`** (ensurepip bug
  with the venv stub spawning python.exe via unquoted paths). Workaround in
  use: install cloud deps via `python -m pip install --user -e ".[dev]"` and
  run with system Python. virtualenv has the same issue. The clean fix is
  Python 3.11+, but we're sticking with 3.9 to avoid the upgrade.
- **Git on Windows** logs CRLF warnings on every commit. Ignore them.

---

## How to invoke this on resume

Paste this prompt into a fresh Claude Code session:

> Resume Knight Rider. Read `CONTINUE.md`, then `git log --oneline -15`. The
> architecture is locked in memory — don't re-derive it. Pick the top item
> from "Up next" and propose how you'd tackle it before writing code. When
> you finish a chunk, your last step is to update CONTINUE.md and commit it.
