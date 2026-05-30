# Drive-Capture Protocol — data the GP can actually learn from

**Why this exists.** The first GP pass on `axio-only-20260529` produced a
degenerate feature (`rstd(rstd(throttle)) / same` ≈ constant). Cause: that
session is **idle + stationary** (`speed = 0` throughout) and **already faulty**
(P0420 stored the whole time). The RA-Typed GP scores features for being
*tight/stationary on healthy data* and then checks whether they flag faults — so
it needs (a) dynamics (driving) and (b) a healthy↔faulty contrast. This protocol
collects both.

Pipeline that consumes the output is ready:
`captures/*.wide.csv` → `discovery_track/typed_gp/loaders.load_knight_rider_obd`
→ `obd_ts()` → `core.run_gp`. See `_smoke_obd_real.py`.

---

## 0. One code change BEFORE the drive — capture upstream O2

Catalyst efficiency (P0420) is diagnosed by comparing **upstream** O2 (pre-cat,
switches rapidly in closed loop) vs **downstream** O2 (post-cat, should stay
steady on a good cat; mirrors upstream on a bad one). We currently capture **only
downstream** (`obd.o2_b1s2_v`, PID 0x15). Without upstream, the catalyst feature
is uncomputable — the loader's `o2_up` terminal is empty.

**The Axio does NOT support narrowband PID 0x14** (not in its supported-PID scan)
but **does support PID 0x24** (O2 Sensor 1 wide-range / AFR — equivalence ratio +
voltage). So upstream O2 on this car = **PID 0x24**.

Action (small Rust change in `src/can/obd.rs` + `obd_poller.rs`, then redeploy):
- Add `ObdPid::O2S1WrLambda = 0x24` (4 data bytes: `λ=(A·256+B)·2/65536`,
  `V=(C·256+D)·8/65536`); emit `obd.o2s1_eq_ratio` (and/or `obd.o2s1_v`).
- Map it to the GP's `o2_up` terminal (already wired in
  `loaders.KR_SIGNAL_TO_OBD`; add the new signal name there).
- Optionally also add `0x2C` (O2S5 AFR, also supported) for bank coverage.
- Redeploy: `git pull && cargo build --release && sudo systemctl restart knight-rider`.
- Verify at the car: `curl http://kitt.local:8080/ws/live` shows `obd.o2s1_*`
  changing while the engine is warm and running.

---

## 1. Sessions to capture (label each with a fresh device_id/buffer)

Capture the **same protocol on two cars** for a clean contrast:
- **Axio (E160)** — the *faulty* reference (stored P0420).
- **Vitz (NSP130)** — the *healthy-ish* reference (no stored catalyst code), if
  available. Different engine, so treat as supporting contrast, not ground truth.

Even Axio-only is usable: the GP trains on the *warm cruise* segments (the car's
own "normal envelope") and we look for the catalyst signature emerging on
overrun/load transitions. Two cars just make the contrast cleaner.

Give each run its own buffer so sessions don't co-mingle:
```bash
ssh kitt@kitt.local "sudo systemctl stop knight-rider; \
  sudo mv /var/lib/knight-rider/buffer.sqlite \
          /var/lib/knight-rider/buffer.$(date +%s).bak; \
  sudo systemctl start knight-rider"
```

## 2. The drive cycle (~15–20 min, engine fully warm = closed loop)

The point is to **excite** the signals the catalyst monitor cares about:
fuel-trim corrections, O2 switching, and load transitions.

1. **Cold start + idle, 2–3 min** — captures warm-up (coolant ramp), useful for
   the thermostat signature too. Stay parked.
2. **Warm-up confirm** — wait until `obd.coolant_temp` ≳ 80 °C (closed loop).
   *Everything diagnostic happens after this.*
3. **Gentle accel runs ×4–5** — 0→50 km/h easy, light-to-moderate throttle. Each
   loads the engine and drives O2/trim dynamics.
4. **Steady cruise ×3** — hold ~40, ~60, ~80 km/h for ~60 s each (steady-state
   closed loop; the cleanest "healthy envelope" for training).
5. **Decel / overrun ×4** — lift off the throttle from cruise and coast (fuel
   cut). Overrun is the strongest O2 excitation — best catalyst contrast.
6. **A few idle↔rev cycles** while parked at the end (throttle blips).

Drive normally and legally; varied load is what matters, not speed. ~15 min of
mixed driving covers all of the above.

## 3. After the drive — produce the GP frame
```bash
scp kitt@kitt.local:/var/lib/knight-rider/buffer.sqlite \
    "C:\Users\Hp 830 G5\KnightRider\captures\axio-drive-$(date +%Y%m%d).sqlite"
cd "C:\Users\Hp 830 G5\KnightRider\captures"
python decode_buffer.py axio-drive-YYYYMMDD.sqlite   # → .wide.csv
```
Then run the GP:
```bash
cd ../discovery_track
python -c "import random,numpy as np; from typed_gp import *; from typed_gp import core; \
random.seed(0); np.random.seed(0); \
tr,term,fs=load_knight_rider_obd(r'..\captures\axio-drive-YYYYMMDD.wide.csv', fs_hz=5.0, n_windows=24); \
print('terminals:',term); \
[print(f'{f:.3f}',t.expr_str()[:70]) for f,t in core.run_gp(obd_ts(terminals=term),tr,fs,pop_size=200,generations=60,verbose=False)[:8]]"
```
Expect non-degenerate features now: things like `rstd(o2_down)`,
`o2_up div o2_down`, `rstd(stft)`, fuel-trim/airflow ratios — features that are
tight on warm cruise and deviate on the catalyst/overrun segments.

## 4. Quality gates (did the capture work?)
- `obd.coolant_temp` reaches ≳ 80 °C (we got into closed loop).
- `obd.o2_s1*` **and** `obd.o2_b1s2_v` both vary (both sensors captured + alive).
- `obd.rpm` and `obd.maf` show real transients (not flat idle).
- Session ≥ ~12 min of warm running.

If any gate fails, the GP frame will be weak — re-drive rather than burn a GP run.
