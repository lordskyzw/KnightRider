# Scaling-up notes

Things deliberately deferred past the MVP. The MVP ships **one car (the Toyota
Vitz), bundled in the APK**. Everything here is for when we support more than
one vehicle / more than one user.

---

## Per-vehicle 3D car models — cloud delivery keyed off VIN

**Status:** deferred. MVP bundles `mobile/assets/cars/vitz.glb` (real CC-BY
photogrammetry Vitz) directly in the APK. See [[car-3d-model-decision]] memory.

**Why defer:** with one car, bundling is simplest and works fully offline. The
machinery below only earns its keep once there are several cars to choose from.

### The idea
Move the car GLB out of the APK and fetch the right one per vehicle:

1. Pi already reads the **VIN** over OBD-II (Mode 09 → `session.vin`). The VIN
   encodes make/model (first 3 chars = WMI = manufacturer).
2. App captures + **persists** the VIN string while on the Pi LAN (tiny, free).
3. **Whenever the phone next has internet** (not necessarily at the car), it
   asks the cloud for that vehicle's model and caches the GLB locally — the
   same store-and-forward pattern as the batch courier. Bundled Vitz is the
   offline fallback until the real one arrives.

**Key property:** does NOT require LAN + internet simultaneously. VIN capture
(LAN) and GLB fetch (internet) are decoupled in time. In our hotspot topology
the phone usually has both at once anyway (iPhone hotspot = local net + cellular
passthrough), but the design must not depend on it. The Pi is never in the GLB
path — GLB goes cloud → phone directly.

### Cloud side: prefetch/curate, NOT real-time Sketchfab lookup
Decided 2026-05-29. Do **not** have the cloud call the Sketchfab API per
request:
- Sketchfab download URLs are signed + **expire in 300s**, and downloads are
  **zips** needing unpack/convert → multi-second latency on a live request.
- Real-time = serving whatever random model matches the VIN: uncontrolled
  licence (could be CC-BY-**NC** / no-derivatives), poly count, quality.
- Couples our uptime to Sketchfab's and puts a token on the hot path.

Instead **curate**: pull each vehicle GLB once (Sketchfab token used only as an
*ingestion* credential), vet licence + quality, optimize, store the finished
GLB in the cloud. Per-request path just serves a static file + manifest entry.

### Proposed shape
- New knight-rider-cloud endpoint, e.g. `GET /vehicle-asset?vin=...`
  (or `?make=&model=` from a Pi-derived hint), returns:
  ```json
  { "glb_url": "...", "credit": "Vitz by Driving501 · CC BY 4.0",
    "license": "CC-BY-4.0" }
  ```
- Curated manifest (vehicle → glb + licence/attribution). Adding a car =
  one manifest row + one stored GLB; no app release needed.
- VIN→model mapping starts as a **simple curated lookup** (WMI + model hint),
  not a full VIN decoder — VIN decoding isn't standardised across makes. Expand
  as we meet more cars.
- Attribution travels as the `credit` field (data-driven), replacing the
  hardcoded `kVehicleModelCredit` constant.

### App side (was about to build, now deferred)
- Persist captured VIN.
- `VehicleAssetService`: given a VIN, return a cached local GLB path; if missing
  and online, fetch from cloud, cache under app docs (`vehicle_models/`), store
  credit alongside; on any failure return null → caller falls back to bundled
  `assets/cars/vitz.glb`.
- Open question to resolve at build time: how `model_viewer_plus` loads a
  *downloaded file* (vs a bundled asset) — file:// path vs network URL vs proxy.
  Verify against the package source before committing to a caching approach.
- Wire `CarModel3D` to prefer the cached path, else the bundled asset.

---

## 3D model load time

The Vitz GLB is 3MB with 152 mesh primitives / 37 materials. Cold-start in the
WebView (proxy + model-viewer.min.js + WebGL init + parse) is noticeably slow,
worst on emulators (software GL). MVP masks it with a loading spinner behind the
transparent viewer. To actually cut load time later:
- Merge/weld geometry (152 primitives → few) and prune via `gltf-transform`.
- Compression: prefer **meshopt** over Draco — Draco's decoder is fetched from
  gstatic by default and would break the offline-first case. Verify model-viewer
  ships the meshopt decoder inline before relying on it.
- Blocked 2026-05-29: `npx @gltf-transform/cli` failed with npm `ECOMPROMISED`
  in this environment; revisit when the npm toolchain is healthy.
- Bigger win long-term: native Filament renderer (`thermion`) instead of the
  WebView, which removes the model-viewer.js + WebView warm-up entirely.

## Other deferred items (demo-phase omissions)
From [[knight-rider-system-architecture]]: auth, OTA, ed25519 batch signing,
Pi↔phone pairing. Multi-user / multi-device all live past MVP too.
