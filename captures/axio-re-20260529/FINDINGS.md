# Toyota Corolla Axio — CAN/OBD field reverse-engineering

**Date:** 2026-05-29 (~08:58–09:23 UTC)
**Car:** Toyota Corolla Axio (E160-series), IG-ON / engine-off then engine-running
**Pi:** `kitt`, device_id `65897622-52be-4806-9c29-8a0ad8d56a25`, can0 @ 500 kbit/s
**Method:** baseline-vs-active candump capture + per-byte diff (`candiff.py`) and
single-capture oscillation finder (`canosc.py`). Each control tested against a
**fresh** hands-off baseline captured immediately before (drift between two
back-to-back hands-off windows = zero, so slow counters like `2C4`/`380.6`/`640.5`
are known noise and excluded).

## Bus topology

Standard OBD-II works (Mode 01/03/09 all answer on `7E8`). The OBD-II port
exposes the **powertrain/chassis CAN**. A gateway bridges *some* body-ECU
status (doors, brake-lamp echo) but **not** exterior lighting.

## Signal map (field-verified)

| Signal | On OBD-II bus? | Frame | Byte/bit | Evidence |
|--------|:---:|------|----------|----------|
| Vehicle speed | ✅ | `0x0B4` | byte5..6 @0+ | sniffer decodes it (Prius DBC subset coincidentally matches); 0 mph parked |
| Brake — switch | ✅ | `0x224` | byte0 **bit5** (`0x20`) | `00`→`20`, 248× @ ~41 Hz, hold/release clean |
| Brake — stop-lamp echo | ✅ | `0x3B4` | byte4 **bit0** | `80`→`81`, slow body msg, brake only |
| Driver door open | ✅ | `0x620` | byte5 **bit5** (`0x20`) | `40`→`60`, 20× @ ~3 Hz |
| Turn signal (L) | ❌ | — | — | blinking produced **zero** new oscillating byte |
| Hazards | ❌ | — | — | flashing produced **zero** diff (= both turn signals) |
| Headlights (beam) | ❌ | — | — | beam-on produced **zero** clean diff vs fresh baseline |

**Implication for the 3D-car live-lights feature:** brake lights and driver-door
state CAN be driven from real CAN on the Axio; **turn signals and headlights
cannot** (they live on a body bus behind the gateway) — keep those on the manual
Settings/demo override.

## OBD telemetry (engine running, all 14 PIDs read correctly)

RPM 0→2602 · coolant 25→63 °C (warm-up ramp) · engine_load 0→64 % ·
timing_advance 2.5→35° · O2 B1S2 0→0.85 V (healthy switching) · MAF 0.07→6.6 g/s ·
STFT −10..+6 % · LTFT +5.5..+11 % · throttle ~19 % · speed 0 (parked).

## Stored DTC

**`P0420` — Catalyst System Efficiency Below Threshold (Bank 1)** is stored on
this Axio (Mode 03 sweep, dtc_count=1). Real fault, not injected.

## Battery / alternator (Mode 01 PID 0x42 Control Module Voltage)

| State | Voltage |
|-------|---------|
| IG-ON, engine off | **11.89 V** (battery resting under accessory load — on the low side; healthy rest is ~12.4–12.6 V) |
| Engine running | **14.08–14.10 V** (stable) |

+2.2 V jump = **alternator + regulator healthy**, charging normally. PID 0x42 is a
clean battery/charging-health signal; **add it to the poller**. No standard PID for
alternator *current* (manufacturer-specific only).

## Supported Mode 01 PIDs (39 total — we poll ~13)

```
01-20: 01 03 04 05 06 07 0C 0D 0E 0F 10 11 13 15 1C 1F 20
21-40: 21 24 2C 2E 30 31 33 34 3C 3E 40
41-60: 42 43 44 45 46 47 49 4A 4C 4D 4E
```

High-value PIDs not currently polled: **0x42** (battery/alternator),
**0x3C/0x3E** (catalyst temp B1S1/B2S1 — relevant to the stored P0420; B1S1 read
184.8 °C warming), **0x44** (commanded λ), **0x43** (absolute load), **0x33**
(barometric pressure, good for discovery-track normalization), **0x46** (ambient
air temp), **0x4D/0x4E** (time with MIL / since codes cleared).

## Vehicle identification (Mode 09 / UDS) — VIN NOT available

- Mode 09 supported-PID bitmap `0x3C000000` → only PIDs 03/04/05/06. **VIN
  (PID 02) unsupported.**
- Mode 09 PID 04 **Calibration ID = `31254100`** (the one readable identifier).
- UDS `22 F190` → `7F 22 11` (service-not-supported) on engine ECU + module `7C8`;
  needs an extended-session unlock (write/session action, out of scope).
- Functional-broadcast VIN: no responder.
- **Implication:** the app must identify the car via a picker / manual VIN entry,
  not OBD VIN. (Pi binary also only reads VIN at startup — should retry on first
  CAN traffic.)

## Other IDs seen

- `0x0B0`/`0x0B2`/`0x0B4` @ ~83 Hz — wheel/speed group
- `0x260`/`0x262` @ ~50 Hz — steering
- `0x0BA` — steering-angle sensor (oscillates at rest; noise for diffing)
- `0x398` — new ID appears only with engine running (slow-rising counter `01F4`→`02xx`)
- No `0x1C4` (Prius RPM ID) — RPM on the Axio is OBD-poll-only, not broadcast.

## Files in this session

- `captures/axio-buffer-20260529.sqlite` — full Pi buffer snapshot (Vitz 28th + Axio 29th)
- `captures/axio-only-20260529.{sqlite,csv,wide.csv,json,summary.md,pdf}` — Axio-only (created_at ≥ 2026-05-29)
- `captures/axio-re-20260529/*.log` — raw candump windows per control
- `captures/axio-re-20260529/{candiff,canosc}.py` — the diff/oscillation tools
