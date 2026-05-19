# Reproducibility Package — Discovery Track v5

## What This Is

A self-contained package to reproduce the v5 reliability-aware GP feature
evolution experiment from the KnightRider diagnostic framework.

## Quick Start

```bash
cd discovery_track/
python run_discovery_v5.py          # Single run (seed=42, ~26 min)
python run_robustness.py            # Multi-seed check (5 seeds, ~2-3 hours)
```

## Requirements

```
numpy>=1.20
scipy>=1.7
scikit-learn>=1.0
pandas>=1.3
```

## File Manifest

| File | Purpose |
|------|---------|
| `gp_engine.py` | Type-safe GP engine (Node, operators, tree generation, mutation, crossover) |
| `run_discovery_v5.py` | Main v5 experiment: reliability-aware GP evolution |
| `run_robustness.py` | Multi-seed robustness validation |
| `reproducibility/PARAMETERS.md` | All hyperparameters and their justification |
| `reproducibility/README.md` | This file |

## Key Parameters

- **Fitness:** (tightness + stationarity + headroom + reliability) / 4
- **No depth penalty** — reliability replaces it
- **Population:** 200, Generations: 80, Max depth: 4
- **Healthy trials:** 60 (training) + 20 (held-out)
- **Split-half:** Each 25s trial cut at midpoint for reliability computation
- **Dedup threshold:** |Pearson r| > 0.95 on reference trial
- **rstd penalty:** 0.5x if rstd at expression root

## Pendulum Physics

Equation of motion:
```
d²θ/dt² = -(g/L)sin(θ) - b·dθ/dt + Fa·sin(Ff·t)
```

| Parameter | Healthy | Fault |
|-----------|---------|-------|
| b (damping) | 0.30 | 0.90 (high_damp) |
| L (length) | 1.00 | 0.55 (short_L) |
| Fa (forcing) | 0.00 | 1.30 (forcing) |

Simulation: T=25s, fs=200Hz, noise=0.006 rad (θ), 0.003 rad/s (ω)

## Expected Results (seed=42)

- 38/50 features with reliability > 0.9
- 35/50 features with AUC > 0.70 at severity=0.25
- 6/6 physics terms rediscovered
- Top feature: cos(9.81·θ) × rstd(rstd(ω)), AUC≈0.86
- Mann-Whitney high_damp: p < 0.05 (supports H3)

## Seed Sensitivity

Results validated across seeds {42, 7, 123, 99, 2024} — see `run_robustness.py`
output for cross-seed statistics.

## Citation

If using this work, cite:
- GP engine: custom typed GP with physical type constraints
- Composite: unsupervised scoring via distribution tightness, stationarity,
  kurtosis headroom, and split-half reliability
- No supervised labels used during feature discovery or scoring
