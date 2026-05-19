# Knight Rider

Two things in one repo: a Rust-based OBD-II diagnostic tool for Raspberry Pi, and a typed Genetic Programming framework for unsupervised fault detection in time series data.

---

## 1. OBD-II Diagnostic Tool (Rust)

A field-grade ECU diagnostic computer for Raspberry Pi.

- Runs on Raspberry Pi 4/5 with Linux (Raspberry Pi OS Lite)
- Communicates directly with vehicle ECUs via CAN / OBD-II
- Operates fully offline — no phone, no cloud, no external dependencies
- Logs data locally with rotation
- Designed for reliability in hostile automotive environments

### Hardware Requirements

| Component | Specification |
|-----------|---------------|
| Computer | Raspberry Pi 4 or 5 |
| CAN Interface | MCP2515 CAN HAT (SPI) or USB-to-CAN adapter |
| OBD-II Connector | Standard 16-pin J1962 (Pins 6 & 14 = CAN-H/CAN-L) |
| Power | 12V → 5V buck converter, 3A minimum |

### Building and Usage

```bash
# Build on Raspberry Pi
cargo build --release

# Run with real CAN interface
./target/release/knight-rider --interface can0

# Run with virtual CAN (testing)
./target/release/knight-rider --interface vcan0

# Diagnostic check mode
./target/release/knight-rider --check
```

Cross-compilation from Windows/macOS requires the ARM target (`rustup target add aarch64-unknown-linux-gnu`).

---

## 2. GP Feature Discovery Framework (Python)

A typed Genetic Programming engine that discovers interpretable fault-detection features from raw sensor data. The GP trains only on healthy/normal data and produces symbolic expressions that combine physical sensors — no labels, no neural networks, no black boxes.

### Key Ideas

- **Typed GP**: Expression trees are constrained by a physical type system (e.g., Temperature, Pressure, Speed). Only physically meaningful combinations are explored.
- **Unsupervised**: Fitness is evaluated entirely on healthy data using a v5 criterion: tightness + stationarity + headroom + reliability. No fault labels needed.
- **Domain-adaptable**: Each dataset defines its own type system and terminal set. The GP operators (ddt, rstd, rmean, abs, neg, add/sub, mul/div) remain the same.
- **Interpretable output**: Results are symbolic expressions like `(NRc - NRf) + |Nf|` rather than opaque model weights.

### Validated Datasets

| Dataset | Domain | Result |
|---------|--------|--------|
| Synthetic pendulum | Physics sim | GP matches hand-crafted energy feature |
| CWRU Bearing | Vibration (12kHz) | GP discovers envelope-energy features, AUC 1.000 |
| IMS Bearing | Vibration (20kHz) | GP detects 11 days before failure (34% of life) |
| EngineFaultDB | Automotive vibration | AUC 1.000, discovers TPS×RPM load composite |
| NASA C-MAPSS FD001 | Turbofan run-to-failure | GP detects at 35.2% of life vs 37.4% baseline |
| MIT-BIH Arrhythmia | ECG (360Hz, 48 patients) | 0.887 AUC, novel cross-lead features |

Full results and analysis: [`discovery_track/FINDINGS.md`](discovery_track/FINDINGS.md)

### Running Experiments

```bash
pip install numpy pandas scipy

# Synthetic pendulum (quick sanity check)
python experiments/stage1_pendulum.py

# CWRU bearing fault detection
python real_world/run_cwru.py

# IMS bearing degradation tracking
python real_world/run_ims.py

# NASA C-MAPSS turbofan run-to-failure
python real_world/run_cmapss.py

# MIT-BIH ECG arrhythmia detection (downloads from PhysioNet)
pip install wfdb
python real_world/run_mitbih.py

# EngineFaultDB automotive faults
python real_world/run_enginefaultdb.py

# Core GP engine with injection/robustness tests
python discovery_track/run_discovery_v5.py
python discovery_track/run_injection.py
python discovery_track/run_robustness.py
```

Datasets are not included in the repo (too large). Download them separately into `real_world/` — see each script's header for source URLs and expected paths.

---

## Project Structure

```
KnightRider/
├── src/                        # Rust OBD-II diagnostic tool
│   ├── can/                    #   CAN bus interface layer
│   ├── core/                   #   Application logic
│   ├── logging/                #   Data logging
│   └── main.rs
├── scripts/                    # Pi setup scripts
├── tests/                      # Rust integration tests
│
├── discovery_track/            # GP research framework
│   ├── gp_engine.py            #   Core typed GP engine
│   ├── run_discovery_v5.py     #   Main GP experiment runner
│   ├── run_framework.py        #   Framework evaluation
│   ├── run_injection.py        #   Fault injection tests
│   ├── run_robustness.py       #   Robustness analysis
│   ├── FINDINGS.md             #   All experimental results
│   ├── paper/                  #   LaTeX paper drafts
│   └── reproducibility/        #   Reproducibility docs
│
├── real_world/                 # Real-world dataset experiments
│   ├── run_cwru.py             #   CWRU bearing (12kHz vibration)
│   ├── run_ims.py              #   IMS bearing (20kHz vibration)
│   ├── run_cmapss.py           #   NASA C-MAPSS FD001 turbofan
│   ├── run_mitbih.py           #   MIT-BIH ECG arrhythmia
│   ├── run_enginefaultdb.py    #   EngineFaultDB automotive
│   └── run_cwru_cross.py       #   CWRU cross-condition test
│
├── experiments/                # Early-stage experiments
│   ├── stage1_pendulum.py      #   Synthetic pendulum validation
│   ├── stage2_obd.py           #   OBD-II GP exploration
│   └── run_obd_gp.py           #   OBD GP runner
│
├── docs/                       # Documentation
├── Cargo.toml                  # Rust project manifest
└── README.md
```

## License

MIT
