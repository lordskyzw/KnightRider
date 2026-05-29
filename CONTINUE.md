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
the **cloud** (FastAPI on Railway) when off-LAN. Cloud will run the
**discovery track** (RA-Typed GP) against accumulating data — that's the
research-publication path (already validated on 9 published experiments).
A **known track** runs on the Pi for instant local alerts. The
discovery→known-track promotion loop closes via cloud-built signed bundles
delivered back to the Pi `/inbox` by courier phones.

Demo phase = no auth on LAN, no ed25519 yet, no OTA. Single Pi, single car.

---

## Resume protocol

When you start a session:

1. **Read this file.**
2. `git log --oneline -15` — see what's actually shipped.
3. The auto-loaded memory in `.claude/projects/.../memory/MEMORY.md` already
   gives you the architecture. Don't re-derive it; don't ask the user to
   re-explain.
4. Pick the next item from **Up next** below and propose it (don't just
   dive in if it's non-trivial — confirm with the user first).

When you finish a chunk of work, before stopping:

1. Update this file: move the chunk from "Up next" to "Shipped" with the
   commit SHA. Refine the remaining backlog if your work changed it.
2. Commit `CONTINUE.md` with a `docs: update CONTINUE.md` commit (or fold
   into your feature commit).

---

## Shipped so far

### This repo (`KnightRider`)

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| `20cf44f`| Extractor scaffolding: Sample broadcast channel, obd_poller migrated off main.rs, sniffer stub. |
| `9e8a16d`| Versioned `BatchEnvelope` (CBOR, split envelope/payload versions) + SQLite WAL `Store` with monotonic batch_id that survives reboots. |
| `12edb30`| axum server on `0.0.0.0:8080`: `/health`, `/ws/live`, `/backlog?since=N` (NDJSON, envelope_b64 verbatim), `/known-track/alerts` + `/inbox` stubs. |
| `73d2b6b`| `main.rs` rewired to tokio service: CAN → broadcast → buffer-writer + server. Race-free subscribe order. ctrl-c flushes writer. |
| `cc7dbdb`| Flutter mobile under `mobile/`: dark dashboard (RPM big + 5 small gauges + backlog counts + status chip), `WsClient` w/ 2s auto-reconnect, sqflite backlog store, settings screen. |
| `5c4653e`| Pi `/backlog` rows now include `device_id` (pulled from envelope) — cloud's `(device_id, batch_id)` idempotency key needs it. |
| `1ef4e54`| Flutter courier uploader. `BacklogDb` v2 with `device_id` column. `Uploader` (10s tick, 100-row chunks). `PiConfig.cloudUrl` + Settings field. Dashboard backlog card shows `uploaded/total` + colored cloud-upload heartbeat. |
| `a8f6a76`| Mobile: add INTERNET + ACCESS_NETWORK_STATE permissions to release manifest (Flutter only adds INTERNET to debug/profile by default; release APKs had EPERM on every socket). Plus state-tightening: `_backlogPullError` clears on success; `_wsError` clears on CONNECTING; first sample resets the WS-error banner. |
| `1072af2`| `docs/`: tiny Python http.server (`docs/serve.py`) + Procfile + railway.toml so the doodle-themed landing page can run on Railway since GitHub Pages was down. |
| `cb2fffa`| `docs/index.html` refreshed to match current state — status "Field Testing", three-tier system narrative, new Research section with `+19.2h / +2.2% / 6/6 physics` headline numbers, 5-node roadmap, links to showcase + sister repos. |
| `e7f0b33`| Mobile: surface app build version on the Settings screen and translate `errno=1` in the diag banner into "you have an old APK, reinstall". Cuts the "is everyone on the latest build?" debugging round-trip. |
| `a6b2f72`| **Tonight's big push.** OBD poller now polls **14 PIDs** (was 6) — engine load, MAP, MAF, timing advance, STFT/LTFT B1, O2 B1S2 voltage, run-time-since-start, plus the original five. Added Mode 03 stored-DTC sweep every 30s with diff-tracking (emits `dtc.stored.p0301` on new, `dtc.cleared.*` on drop, `obd.dtc_count` heartbeat). Added Mode 09 VIN/cal-id/ECU-name reads at startup, manual flow-control for multi-frame responses. New `src/can/dtc.rs` module + tests. Sniffer wired up for real: hardcoded Toyota Prius 2010 DBC subset (`0x1C4` ENGINE_RPM @ ~42 Hz, `0x0AA` WHEEL_SPEEDS × 4, `0x0B4` SPEED). `main.rs` now opens two CAN sockets — one for the poller, one for the sniffer. 41 tests pass (was 35). |

#### Mobile 3D dashboard arc (2026-05-29) — versions 0.3.0+3 → 0.7.0+10

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| `3bf55d1`| Tesla-style dashboard + drill-down screens (STORED/SYNCED/WAIT → BatchesScreen, DTC → DtcScreen w/ ~80-code lookup) + Vitz SVG silhouette in centre. |
| `cc46ea7`| Rotatable 3D car (`model_viewer_plus`, glTF) in dash centre behind a Settings feature flag; SVG fallback. (Chose glTF/model-viewer over Unity — see `car-3d-model-decision` memory.) |
| `7764135`| Swapped placeholder for the **real CC-BY Toyota Vitz** GLB (Driving501, Sketchfab, pulled via Sketchfab Data API w/ user token). On-view attribution. |
| `1997c17`| Knight Rider app icon — KITT red scanner bar, all densities + adaptive, via `flutter_launcher_icons`; reproducible from `mobile/tool/gen_icon.py`. |
| `9bb6785`,`657de6b`| `docs/SCALING.md`: deferred VIN-keyed cloud GLB delivery (curate-not-realtime); live-model-state (lights/doors) feasibility. |
| `9c54e2d`| App-wide Tesla theme (`app_theme.dart`) + user accent picker; **fixed WS pill flapping** (ws_client no longer marks connected optimistically); perf: dashboard coalesces samples to ~12 Hz repaint; 3D default ON, bigger/brighter. |
| `982d4b2`| Body-only car paint (tint the `Paint` material only) + 3D load spinner. |
| `de3a303`| Car **lights** (emissive on lamp materials) + **wheel colour** (default black) pickers; calm offline banner (no raw SocketException); GLB optimised `gltf-transform weld+prune` 3.22→2.07 MB (needed npm 11.6.2→11.16.0 for the ECOMPROMISED bug). |
| _pending_| Dark launch splash (was white); **next:** wire lights to live DBC signals. |

#### Toyota Axio field session + extractor expansion (2026-05-29)

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| _pending_| **Extractor expansion from the Axio field session.** OBD poller default set grew 14→20 PIDs: battery/charging voltage (0x42), absolute load (0x43), barometric pressure (0x33), catalyst temp B1S1/B1S2 (0x3C/0x3E, for the Axio's stored P0420), commanded λ (0x44). Sniffer `Signal` gained a `Field` enum (`Bytes` **+ new `Bit`**) so boolean body signals decode; added field-verified Axio profile: brake `0x224.0` bit5, stop-lamp `0x3B4.4` bit0, driver door `0x620.5` bit5 (→ `dbc.toyota.BRAKE.pressed` / `.STOP_LAMP.on` / `.DOORS.driver`). 45 tests pass (was 41). **Not yet deployed to the Pi or live-verified on a car.** |

### Sibling repo `../knight-rider-cloud`

| Commit   | What                                                        |
|----------|-------------------------------------------------------------|
| `e9ffa16`| Cloud ingest API. FastAPI + SQLModel. `POST /v1/batches`, idempotent on `(device_id, batch_id)`. SQLite local + Postgres on Railway. 8 pytest tests pass. |
| `e0907c2`| Flatten src/ layout + add `requirements.txt` for nixpacks (the src-layout + pyproject-only path broke Railway's build because nixpacks runs `pip install .` before copying app code). |
| `e6f47..`| Idempotency fix: use `.returning()` instead of `result.rowcount` (psycopg3 + ON CONFLICT DO NOTHING didn't report rowcount reliably; every Postgres POST was returning `duplicates:N`). Set `received_at` explicitly. |

**Deployed and live at `https://knight-rider-cloud-production.up.railway.app`**
under The Janitors workspace. `POST /v1/batches` verified end-to-end.

### Sibling repo `../ra-typed-gp-showcase`

Streamlit web app showcasing the discovery-track research. **Live at
`https://ra-typed-gp-showcase-production.up.railway.app`** under The Janitors
workspace. Not yet pushed to GitHub — direct Railway upload only.

Twelve sections: hero · question · framework · journey (v1→v5) · experiment
explorer (all 9 published experiments) · sensitivity-stability paradox
(interactive) · cross-domain summary · **seven live-evolve sections** that run
the actual GP from scratch in-browser (pendulum, OBD/engine, CWRU bearings,
IMS multi-bearing, EngineFaultDB, NASA C-MAPSS turbofan, MIT-BIH ECG) ·
online-discovery (GP at 3/8/15/25 trial counts) · KnightRider system context ·
reproducibility.

Architecture: shared `gp_core.GpEngine` parameterised by per-domain configs in
`domains/`. Each `domains/*.py` ports the TYPES/TERMINALS/CONSTS/OPS from the
matching `KnightRider/real_world/run_*.py` runner plus a synthetic signal
generator sized for browser-fast runs (pop 30 × gen 12, ~30s each).

### Sibling repo `kr-landing` (Railway service)

Doodle-themed marketing site at `https://kr-landing-production.up.railway.app`.
Source lives in `docs/` of this repo. Updated content to reflect current state.

### Field testing milestones (2026-05-28)

- **Toyota Vitz DBA-NSP130 (NSP130 chassis, post-2010)** — end-to-end **CONFIRMED**.
  Standard OBD-II works. Mode 01 PID 0x00 bitmap `BE 3F A8 13` (18 supported PIDs).
  RPM/coolant/intake/throttle decode correctly. 12-minute idle capture =
  4406 samples in `/var/lib/knight-rider/buffer.sqlite`. Decoded copies in
  `captures/vitz-buffer-20260528.{sqlite,csv,wide.csv,json,summary.md,pdf}`.
- **Honda Fit 2007-2013 (GE chassis)** — OBD-II port exposes raw F-CAN
  broadcast frames only. Diagnostic gateway blocks Mode 01/03/09 requests
  (Honda proprietary HDS protocol). 66 distinct broadcast IDs visible at
  IG-ON with engine running. Decoding requires per-Honda DBC, not opendbc.
- **Toyota Wish ZGE20** — wasn't tested cleanly. Pi showed zero RX/TX/errors at every baud,
  with the OBD-II cable possibly loose. Architecturally should work like the Vitz.
- **Nissan Sunny FB15** — K-line at OBD-II pins. MCP2515 can't read it.
  Would need an L9637 K-line transceiver and a KWP2000 stack. Out of scope
  for this demo.

### MCP2515 HAT gotcha
The Waveshare RS485 CAN HAT shipped with a 12 MHz crystal but
`/boot/firmware/config.txt` had `oscillator=16000000`. Wrong by 4 MHz =
33% bit-timing error → controller goes bus-off the moment a real car is
plugged in. Fix: change to `oscillator=12000000` and reboot. **Always
check the silkscreen on the crystal vs the config value before debugging
anything else.**

### iPhone hotspot + mDNS workflow

- Pi auto-joins iPhone Personal Hotspot via NetworkManager (no
  `wpa_supplicant.conf` on Bookworm). Settings persisted via the GUI;
  bumping `connection.autoconnect-priority 100` keeps it preferred. Use
  the UUID (`nmcli -t -f NAME,UUID connection show`) — the SSID contains
  a typographic apostrophe (`'` U+2019) that breaks plain `nmcli` calls.
- mDNS works on iPhone hotspot: Pi hostname set to `kitt`, avahi-daemon
  active → `ssh kitt@kitt.local` resolves from Windows / iPhone Termius
  via IPv6 link-local in ~50 ms.
- SSH key auth set up via `Get-Content $HOME\.ssh\id_ed25519.pub | ssh
  kitt@kitt.local "cat >> ~/.ssh/authorized_keys"`. Passwordless from
  Windows now. Pi user has passwordless sudo (`/etc/sudoers.d/010_kitt-nopasswd`).
- On iPhone: **leave the Personal Hotspot settings screen open** so iOS
  keeps broadcasting (otherwise hotspot dies after ~90s of no clients).
  Also flip **Maximize Compatibility ON** — forces 2.4 GHz, much more
  reliable for the Pi WiFi chip.

### Captures pipeline (under `captures/`)

```
captures/
  decode_buffer.py        # buffer.sqlite -> csv + wide.csv + json + summary.md
  generate_report.py      # decoded.json  -> 4-page PDF report
  vitz-buffer-20260528.*  # today's Vitz session, fully decoded
```

Both scripts take the input file as `sys.argv[1]`. Pipeline for any future
capture: `scp` buffer.sqlite to Windows, `python decode_buffer.py …`, then
`python generate_report.py ….json`.

Test coverage: 41 Rust unit tests + 1 Flutter widget test + 8 cloud pytest
tests, all passing.

---

## Up next (priority order)

### 0. 3D car live-state + renderer (current focus, 2026-05-29)
- **Pi side DONE (pending deploy):** sniffer now has bit-level (`Field::Bit`)
  support + a field-verified Toyota Axio profile (brake `0x224.0` bit5, stop-lamp
  `0x3B4.4` bit0, driver door `0x620.5` bit5). **Key field finding:** exterior
  lighting (turn/hazard/headlight) is **gatewayed off the OBD-II bus** on the
  Axio — only brake + door are recoverable. So *live* lamp state from CAN = brake
  + door only; turn/headlights stay on the manual Settings override forever.
  Full map: `captures/axio-re-20260529/FINDINGS.md`. **TODO: deploy to Pi +
  live-verify** (press brake → `dbc.toyota.BRAKE.pressed` flips to 1).
- **App side (next):** dashboard should consume `dbc.toyota.BRAKE.pressed` /
  `.DOORS.driver` and drive `CarModel3D` brake-light + door live via
  `runJavaScript` (NOT key-reload — that re-triggers the 30s warm-up).
- **Renderer decision:** evaluate native Filament (`thermion`) vs the current
  `model_viewer_plus` WebView. WebView costs the cold-start delay, no bloom (so
  lamps look "full-bright" not glowing), and can't transform nodes (blocks
  animated doors). Filament would fix all three but is a bigger integration.
  See `docs/SCALING.md` "3D model load time" + "live model state".

### 0b. App polish — next major thrust (user flagged 2026-05-29, do AFTER Rust side)
- **Online/offline toast bug:** "unexplainable behaviour" in the connectivity
  toast (false/flapping online/offline). Investigate `ws_client` + connectivity
  state derivation. User's top app complaint.
- **Per-car 3D models:** need distinct GLBs for the cars we actually have (Vitz +
  Toyota Axio E160 at least), chosen via an in-app **car picker** — VIN
  auto-detect is NOT viable (see [[obd-vehicle-id-constraint]]; VIN not OBD-
  readable on Axio/Honda). App currently ships the Vitz GLB only.
- General advancements / polish (TBD with user).
- **Cloud API unchanged** — user confirmed no API changes for this thrust.

### ✅ DONE 2026-05-29 — Axio field session (was items 1 & 2)
- **Extended binary field-tested on a Toyota Corolla Axio E160.** Standard OBD-II
  works; all 14 (now 20) PIDs decode. Stored DTC **P0420** (catalyst efficiency
  B1). Battery/alternator confirmed via PID 0x42 (11.89 V off → 14.10 V running).
  VIN **not** OBD-readable (Mode 09 PID 02 unsupported, UDS 22F190 service-not-
  supported) — see [[obd-vehicle-id-constraint]] memory; cal-ID = `31254100`.
- **Courier loop verified end-to-end on the 14-PID binary.** Phone LAN dashboard
  showed live RPM + P0420; courier drained **453 batches Pi→phone→cloud**,
  confirmed in Railway Postgres (`device 65897622…` count 453, idempotent).
- Captures: `captures/axio-only-20260529.*` + `captures/axio-re-20260529/`.

### 1. (superseded — see DONE above) Field test of the extended binary
Binary is built and **already running on the Pi at
`/home/kitt/KnightRider/target/release/knight-rider`** as of 2026-05-28
~15:45 UTC, waiting for CAN traffic. When the Pi plugs into the Vitz:

- Expect VIN + cal_id + ECU name samples within the first second.
- Expect ~14 OBD samples/sec from the fast loop + ~1200
  sniffer samples per 30s from `0x1C4`/`0x0AA`/`0x0B4`.
- Stored-DTC count emitted every 30s (Vitz at idle should be 0 codes).

Tail the log: `ssh kitt@kitt.local "tail -f /tmp/kr.log"`.

Tomorrow morning's flow:
```bash
scp kitt@kitt.local:/var/lib/knight-rider/buffer.sqlite \
    "C:\Users\Hp 830 G5\KnightRider\captures\vitz-night-$(date +%Y%m%d).sqlite"
cd "C:\Users\Hp 830 G5\KnightRider\captures"
python decode_buffer.py vitz-night-YYYYMMDD.sqlite
python generate_report.py vitz-night-YYYYMMDD.json
```

If you want a fresh device_id for the night capture (so it's not co-mingled
with today's 4406-sample bench/idle data):
```bash
ssh kitt@kitt.local "sudo pkill knight-rider; \
  sudo mv /var/lib/knight-rider/buffer.sqlite \
          /var/lib/knight-rider/buffer.bench-$(date +%s).sqlite; \
  sudo nohup /home/kitt/KnightRider/target/release/knight-rider \
    --interface can0 --buffer /var/lib/knight-rider/buffer.sqlite \
    > /tmp/kr.log 2>&1 &"
```

### 2. Verify Flutter courier loop end-to-end on the new binary
Pi side is producing samples → buffer → /backlog. Untested with the
extended PID set: does the phone parse the larger envelopes correctly?
Does the cloud receive them? Quick test:

- Phone connects to Pi LAN, dashboard should show RPM + speed live.
- Phone goes off LAN, switches to cellular, courier uploads.
- `railway connect Postgres` → `SELECT device_id, count(*), max(received_at)
   FROM batches GROUP BY device_id` should show batches arriving.

### 3. Push `ra-typed-gp-showcase` to GitHub
Currently Railway-only. Same drill as the other two repos — create empty
`github.com/lordskyzw/ra-typed-gp-showcase`, `git remote add origin ...`,
`git push -u origin main`.

### 4. Discovery worker skeleton (cloud-side)
In `knight-rider-cloud`. New `knight_rider_cloud/worker/`. Reads recent
batches from Postgres, CBOR-decodes each envelope (`cbor2`), extracts
samples into a per-device timeseries, runs the RA-Typed GP. For first ship
just decode + write a `discovery_runs` row with a summary; actual GP
integration comes after we know the data shape in practice. Also add
`GET /v1/devices/:id/recent` so the worker (or you) can inspect what
arrived.

### 5. Known-track inference on the Pi
New module `src/known_track/`. Inputs: live `Sample` stream + a loaded
`KnownTrackBundle` (signed cloud artifact). Outputs: alerts to
`/known-track/alerts`. For demo: DTC reading (already wired) + a handful of
threshold rules (e.g. STFT B1 deviation > 10% for >30s → lean-burn alert).
The model-bundle loader (`src/known_track/bundle.rs`) with atomic swap +
previous-bundle rollback is the harder half — but it's the piece the
architecture loop hinges on.

### 6. Wire the showcase's KnightRider section into live cloud data
Once enough Vitz captures have flowed through, the showcase should
display real discovered features against real OBD samples instead of
synthetic data.

### 7. ed25519 batch signing
Adds a `signature` field that's already reserved in `BatchEnvelope`. Pi
generates keypair on first boot, persists privkey in `meta` table,
registers pubkey with cloud on first contact. **Don't ship this until
there's a real cloud-to-Pi loop** — premature otherwise.

### 8. Pi↔phone pairing
Deferred per user. Revisit when leaving demo phase. Likely flow: QR code
shown via a quick CLI helper on the Pi (since Pi has no screen), scanned
by Flutter, exchange a symmetric key cached on each side.

### 9. App / binary OTA
Deferred. Use `git pull && cargo build --release && systemctl restart` on
the Pi for now. Real OTA is ~1-2 weeks of plumbing (signed bundles, A/B
partitions, boot-time rollback) and isn't needed for demo.

---

## House rules (re-state every session — these are easy to drift from)

- **Don't ask the user to re-explain the architecture.** It's in memory.
- **Commit per logical chunk** — credits may run out mid-session; preserve work.
- **Demo phase**: open LAN, no signing, single car. Don't quietly add
  auth/signing/OTA.
- **Pi runs no internet code.** Sync is entirely the phone app's job.
- **Idempotency on cloud ingest is non-negotiable.**
- **Two extractors → one broadcast channel.** Don't fork the pipeline.
- **Phones store `envelope_b64` verbatim.** Don't decode-and-reencode
  mid-courier — the (future) Pi signature has to stay valid end-to-end.
- **OBD-II is read-only.** Modes 01/03/07/09/0A are safe (the Vitz returns
  fine to all of them). Mode 04 (clear DTCs), 2F, 31 write to the ECU and
  are **out of scope** for this project.
- **CRLF warnings on git** are expected on Windows — ignore them.

---

## Quick verification

```bash
# Rust side
cargo test --lib                # 41 tests should pass
cargo check --bins --tests      # no errors (warnings in obd-logger are pre-existing)

# Pi service (when at the bench)
cargo run --bin knight-rider -- --interface vcan0 --bind 127.0.0.1:8088
curl http://127.0.0.1:8088/health
curl 'http://127.0.0.1:8088/backlog?since=0&limit=10'

# Pi service (in the car, after seating OBD cable + IG-ON with engine running)
ssh kitt@kitt.local "tail -f /tmp/kr.log"
# expect: VIN logged, RPM samples flowing, sniffer stats heartbeats every 30s

# Flutter
cd mobile
flutter analyze                 # No issues
flutter test                    # 1 widget test passes

# Cloud (sibling repo, local)
cd ../knight-rider-cloud
python -m pytest -q             # 8 tests should pass

# Cloud (Railway, live)
curl https://knight-rider-cloud-production.up.railway.app/health
# {"status":"ok","version":"0.1.0"}
```

### End-to-end smoke (Pi → phone → cloud)

```bash
# On Pi (assumes binary already built and can0 oscillator value correct)
sudo ip link set can0 down
sudo ip link set can0 up type can bitrate 500000 restart-ms 100
sudo nohup /home/kitt/KnightRider/target/release/knight-rider \
    --interface can0 --buffer /var/lib/knight-rider/buffer.sqlite \
    > /tmp/kr.log 2>&1 &

# In Flutter:
#   Settings → Pi host:port    = <pi-ip>:8080   (e.g. 172.20.10.x on iPhone hotspot)
#   Settings → Cloud base URL  = https://knight-rider-cloud-production.up.railway.app

# Verify on cloud — query Postgres:
railway connect Postgres
\c railway
SELECT device_id, count(*), max(received_at)
  FROM batches GROUP BY device_id;
```

### Decode a captured buffer

```bash
scp kitt@kitt.local:/var/lib/knight-rider/buffer.sqlite \
    "C:\Users\Hp 830 G5\KnightRider\captures\<name>.sqlite"
cd "C:\Users\Hp 830 G5\KnightRider\captures"
python decode_buffer.py <name>.sqlite       # → csv, wide.csv, json, summary.md
python generate_report.py <name>.json       # → pdf
```

---

## Known dev-env quirks

- **MCP2515 oscillator must match crystal.** Waveshare RS485 CAN HAT
  silkscreen says 12 MHz but `/boot/firmware/config.txt` often ships 16
  MHz. Always verify before debugging anything else.
- **SocketCAN doesn't auto-recover** when can0 transitions DOWN→UP after a
  socket is opened. Restart the binary after bringing the link up.
- **iPhone hotspot SSID has a typographic apostrophe** (`Tarmica's iPhone…`
  uses U+2019, not ASCII `'`). Always reference by UUID via `nmcli -t -f
  NAME,UUID connection show`.
- **Spaces in `C:\Users\Hp 830 G5\` break Python 3.9 `venv`** (ensurepip
  bug). Use `python -m pip install --user`.
- **Git on Windows** logs CRLF warnings. Ignore them.
- **Railway CLI `up`** ignores files via gitignore-CRLF bug — always pass
  `--no-gitignore --service <name> --ci`.
- **Railway CLI `add`/`init`** is interactive-by-default. Pass `--name`
  and `--service`; for `add --database postgres` it hangs on a trailing
  prompt — add Postgres from the dashboard if that happens.
- **`DATABASE_URL` reference** is set via
  `railway variables --service knight-rider-cloud --set 'DATABASE_URL=${{Postgres.DATABASE_URL}}' --skip-deploys`.
- **Flutter release APKs** must have `INTERNET` permission in
  `main/AndroidManifest.xml` — Flutter only adds it to debug/profile
  manifests by default, and bare release builds hit EPERM on every socket.

---

## How to invoke this on resume

Paste this prompt into a fresh Claude Code session:

> Resume Knight Rider. Read `CONTINUE.md`, then `git log --oneline -15`.
> The architecture is locked in memory — don't re-derive it. Pick the top
> item from "Up next" and propose how you'd tackle it before writing
> code. When you finish a chunk, your last step is to update CONTINUE.md
> and commit it.
