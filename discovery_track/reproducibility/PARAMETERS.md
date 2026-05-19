# Hyperparameters & Justification

## GP Evolution

| Parameter | Value | Justification |
|-----------|-------|---------------|
| pop_size | 200 | Large enough for diversity, small enough to run in ~26min |
| generations | 80 | Convergence observed by gen 60-70 in all versions |
| max_depth | 4 | Physics expressions (E=½ω²+g/L(1-cosθ)) need depth 3-4 |
| tournament_k | 5 | Standard GP tournament size |
| elite_frac | 0.05 | 10 elites preserved per generation |
| crossover_rate | 0.60 | Standard, slightly below typical 0.7 to allow more mutation |
| mutation_rate | 0.30 | Higher than typical to maintain diversity |
| immigration_rate | 0.10 | Fresh random individuals to escape local optima |

## Fitness Function

| Component | Formula | Range | Purpose |
|-----------|---------|-------|---------|
| Tightness | 1/(1 + H/H_max) | [0.5, 1] | Low entropy = concentrated distribution |
| Stationarity | 1 - CV(chunk_means) | [0, 1] | Stable across time chunks |
| Headroom | 1/(1 + \|kurt\|) | (0, 1] | Low kurtosis = room for fault outliers |
| Reliability | max(0, Pearson(first_half, second_half)) | [0, 1] | Reproducible within trial |

**Combined:** (T + S + H + R) / 4

## Penalties & Gates

| Check | Action | Justification |
|-------|--------|---------------|
| CV < 0.005 | score = 0 | Constant features can't diagnose |
| std < 1e-10 | score = 0 | Degenerate (all same value) |
| len(vals) < 15 | score = 0 | Insufficient data for statistics |
| is_rstd_root | score × 0.5 | rstd at root produces artificially tight distributions |

## Split-Half Reliability

| Parameter | Value | Justification |
|-----------|-------|---------------|
| Split point | midpoint (n//2) | Equal halves, no overlap |
| Min samples for correlation | 10 | Pearson needs at least 10 pairs |
| Clamp negative | max(0, r) | Negative reliability = anti-physics |

## Correlation Dedup

| Parameter | Value | Justification |
|-----------|-------|---------------|
| Threshold | 0.95 | |r| > 0.95 = semantically identical |
| Max keep | 50 | Enough for statistical tests |
| Reference trial | healthy_train[0] | Single trial for consistency |
| Min finite samples | 100 | Robust correlation estimate |

## Data Generation

| Parameter | Value | Justification |
|-----------|-------|---------------|
| N_HEALTHY (train) | 60 | Enough for split-half (30 pairs) + 5-chunk stationarity |
| N_HELD_OUT | 20 | Independent test set for AUC |
| N_FAULT | 40 per severity | Robust AUC estimation |
| Severities | [0.0, 0.25, 0.5, 0.75, 1.0] | 0.25 = early detection, 1.0 = full fault |
| T (trial length) | 25.0 s | ~5 pendulum periods, enough for rolling stats |
| fs (sample rate) | 200 Hz | 5000 samples per trial |
| noise (θ) | 0.006 rad | Realistic sensor noise |
| noise (ω) | 0.003 rad/s | Half of θ noise (derived signal) |

## Summaries

| Name | Function | Used for |
|------|----------|----------|
| std | np.std(output) | Default summary of GP output signal |
| entropy | histogram entropy | Alternative summary (info-theoretic) |

Best summary selected per-feature during fitness evaluation.

## Version History

| Version | Key Change | Result |
|---------|-----------|--------|
| v1 | Single summary (std) | Collapsed to rstd monoculture |
| v2 | +entropy, +hash diversity | rstd gaming (rho=-0.25) |
| v3 | +rstd penalty, +corr dedup, +elitism | Physics at top (rho~0) |
| v4 | Depth penalty 0.05→0.02, +Mann-Whitney | rho=-0.16, ddt(ω²) stays #1 |
| v5 | Reliability IN fitness, no depth penalty | 70% useful, 6/6 physics, MW p=0.039 |
