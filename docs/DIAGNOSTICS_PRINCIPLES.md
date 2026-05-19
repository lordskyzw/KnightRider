# KnightRider Diagnostics — Derivation Engine Principles

> **Purpose of this document:** This is a mantra file. It defines the architectural
> vision, hard constraints, and guiding principles for the KnightRider engine
> diagnostics pipeline. Any code written in this domain must respect these principles.
> When in doubt, return here.

---

## 1. The Derivation Hierarchy

All diagnostic intelligence is built by progressive transformation of raw vehicle data.

```
Level 0 — RAW
  Direct OBD-II PID readings. Untouched CAN frames decoded into physical values.
  Examples: RPM, coolant_temp, MAF, throttle_pos, intake_temp, fuel_trim, O2_voltage

Level 1 — 1st Derivation
  One operation applied to raw signals.
  Examples: Δcoolant/Δt, MAF/RPM, LTFT+STFT

Level 2 — 2nd Derivation
  Two operation steps. Inputs may be any combination of:
    raw + raw, raw + derived(1), derived(1) + derived(1)
  Examples: volumetric_efficiency vs coolant_trend, fuel_trim_deviation × load_ratio

Level N — Nth Derivation
  N operation steps deep. Each level builds on everything below it.
```

### Principle: Depth Costs Credibility

Every derivation level you go deeper **doubles the risk** of producing a
physically meaningless feature. The system must justify depth — a 3rd-level
feature that doesn't outperform a 1st-level feature is waste, not sophistication.

---

## 2. Operations Are Typed, Not Arbitrary

**Never allow unconstrained arithmetic between arbitrary signal pairs.**

Operations are grouped by physical intent:

| Category | Operations | Purpose |
|---|---|---|
| **Temporal** | `Δ/Δt`, `rolling_mean(w)`, `rolling_std(w)`, `lag(n)` | Capture dynamics, trends, oscillation |
| **Ratio** | `a/b`, `(a-b)/b` | Normalise, compare, compute efficiency |
| **Difference** | `a - b` | Thermal deltas, deviation from baseline |
| **Correlation** | `pearson(a, b, window)`, `lag_correlation` | Detect coupling, response delay |
| **Threshold** | `abs(a - baseline)`, `a > limit` | Boundary detection, fault flagging |

### Principle: Physical Typing Constrains the Search Space

Signals have physical dimensions (temperature, flow, ratio, voltage, angular velocity).
Only dimensionally coherent combinations are permitted. You may **difference** two
temperatures. You may **ratio** a flow with a rotational speed. You may **not** divide
a voltage by a temperature — the result has no physical meaning and no diagnostic
interpretation.

Define a type system:
```
Temperature:   coolant_temp, intake_temp, ambient_temp, cat_temp
Flow:          MAF, fuel_rate
Rotation:      RPM
Position:      throttle_pos, EGR_pos
Ratio:         fuel_trim (STFT, LTFT), lambda, volumetric_efficiency
Voltage:       O2_upstream, O2_downstream, battery_voltage
Pressure:      MAP, fuel_pressure, baro_pressure
Time:          engine_runtime, time_since_DTC_clear
```

Permitted cross-type operations are **explicitly enumerated**, not open-ended.

---

## 3. Time Is a First-Class Dimension

OBD-II data is a time series. Every feature must be understood in its temporal context.

### Three Temporal Modes

1. **Instantaneous** — The value right now. `coolant_temp = 92°C`.
2. **Windowed** — Statistics over a sliding window. `std(RPM, 30s) = 12.4`.
3. **Trajectory** — Shape of a signal over an event. `coolant warmup curve shape`.

### Principle: A Snapshot Tells You State. A Trajectory Tells You Health.

A single reading of 92°C coolant is normal. But if it took 20 minutes to reach 92°C
on a warm day, the thermostat may be stuck open. The **shape** of the warmup curve
is the diagnostic — not the final value.

The derivation hierarchy must support temporal operators at every level:

```
Level 0:  coolant_temp[t]
Level 1:  warmup_rate = Δcoolant_temp / Δt  (during cold start)
Level 2:  warmup_health = warmup_rate / expected_warmup_rate(ambient_temp)
```

---

## 4. Feature Pipeline Architecture

### 4.1 Two Tracks, One System

```
┌─────────────────────────────────────────────────────────────┐
│                    RAW CAN / OBD-II DATA                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
    ┌──────────────────┐     ┌─────────────────────┐
    │   KNOWN TRACK    │     │  DISCOVERY TRACK    │
    │                  │     │                     │
    │  Well-documented │     │  Genetic programming│
    │  derived features│     │  + pruning over     │
    │  from automotive │     │  typed operation    │
    │  engineering     │     │  space              │
    │                  │     │                     │
    │  ► Runs on Pi    │     │  ► Runs OFFLINE on  │
    │  ► Real-time     │     │    logged data      │
    │  ► Deterministic │     │  ► Batch processing │
    │                  │     │  ► Stochastic       │
    └────────┬─────────┘     └──────────┬──────────┘
             │                          │
             │  ┌───────────────────┐   │
             └─►│  FEATURE REGISTRY │◄──┘
                │                   │
                │  All promoted     │
                │  features live    │
                │  here with:       │
                │  • derivation DAG │
                │  • normal range   │
                │  • scoring metric │
                │  • physical units │
                └───────┬───────────┘
                        │
                        ▼
              ┌──────────────────┐
              │ ANOMALY DETECTOR │
              │                  │
              │ Flags deviations │
              │ from established │
              │ normal ranges    │
              └──────────────────┘
```

### Principle: Known Features First, Discovery Second

The Known Track gives you **day-one diagnostic capability**. Volumetric efficiency,
fuel trim analysis, warmup curves, catalyst efficiency — these are solved problems
in automotive engineering. Implement them immediately as hardcoded derivation chains.

The Discovery Track is a **background luxury**. It searches for non-obvious
relationships that domain experts haven't documented. It runs on exported log files,
not live data. When it finds a candidate feature that scores well, it gets reviewed
and **promoted** into the Known Track.

A discovered feature that can't be physically interpreted is **suspicious by default**.

### 4.2 The Feature Registry

Every feature — whether hand-coded or discovered — must have a registry entry:

```rust
struct FeatureEntry {
    id: FeatureId,
    name: String,                       // human-readable
    derivation_level: u8,               // 0 = raw, 1 = 1st deriv, etc.
    expression: DerivationExpr,         // the computation DAG
    physical_unit: Option<PhysicalUnit>,// e.g. "°C/s", "ratio", "dimensionless"
    input_signals: Vec<SignalId>,       // leaf dependencies
    normal_range: Range<f64>,           // baseline from healthy data
    scoring_metric: f64,               // information-theoretic score
    source: FeatureSource,             // Known | Discovered { generation, fitness }
    confidence: f64,                   // how much data backs the normal range
}
```

---

## 5. Scoring — How to Judge a Feature Without Labels

You will not have labelled fault data on day one. You may never have enough for
supervised learning. The scoring system must work **unsupervised**.

### 5.1 Information-Theoretic Scoring

| Metric | What It Measures | Why It Matters |
|---|---|---|
| **Entropy** | How tight is the feature's distribution under normal driving? | Low entropy = predictable = easy to spot deviations |
| **Mutual Information** | Does this feature carry info that other features don't? | Avoids redundant features |
| **Stationarity** | Is the feature stable during steady-state driving? | Unstable features during steady-state may indicate fault sensitivity |
| **Temporal Autocorrelation** | Does the feature have predictable time dynamics? | High autocorrelation = smooth signal = anomalies are visible spikes |

### 5.2 Feature Scoring Composite

```
score(f) = w₁·(1 - entropy(f))
         + w₂·unique_info(f)
         + w₃·stationarity(f)
         + w₄·autocorrelation(f)
         - penalty·derivation_level(f)
```

### Principle: Penalise Depth, Reward Tightness

A feature gets **penalised** for every derivation level it's built on — deeper
features must earn their keep. A feature gets **rewarded** for having a tight,
well-defined distribution that makes anomalies obvious.

---

## 6. Discovery Engine — Constrained Genetic Programming

### 6.1 Representation

Each candidate feature is a **typed expression tree**:

```
        ÷
       / \
      Δ/Δt  rolling_mean(30s)
      |          |
  coolant_temp  RPM
```

Nodes are operations (from the typed operation set).
Leaves are raw signals (from Level 0).
The tree's depth = the derivation level.

### 6.2 Constraints (Non-Negotiable)

1. **Max derivation depth: 4.** Anything deeper is almost certainly noise.
2. **Type checking at every node.** The tree must be dimensionally consistent.
3. **No duplicate subtrees.** `a/a = 1` is not a feature.
4. **No degenerate outputs.** Features that are constant, NaN, or infinite on
   training data are killed immediately.
5. **Minimum variance threshold.** If it doesn't vary, it can't diagnose.

### 6.3 Search Strategy

```
1. Initialize population with Level-1 typed combinations
2. Evaluate fitness via composite score (§5.2)
3. Selection: tournament, elitism for top 10%
4. Crossover: swap compatible subtrees (type-safe)
5. Mutation: replace node with type-compatible operation
6. Prune: kill trees that score below threshold or duplicate existing features
7. Promote: features that survive 50+ generations with stable scores → Registry
8. Repeat on fresh log batches to avoid overfitting to one driving session
```

### Principle: Discovery Runs Offline, Promotions Are Manual

The GP engine processes **exported log files** on a capable machine (your PC or
cloud). Discovered features are **candidates** — they get promoted to the Pi's
real-time pipeline only after review. There is no autonomous deployment of
discovered features to the live system.

---

## 7. Normal Range Calibration

### 7.1 The Cold Start Problem

On day one with a new vehicle, you have zero baseline. The system must:

1. **Assume healthy.** The first N hours of data define "normal."
2. **Use conservative ranges.** Wide initial bounds, tightening with data.
3. **Adapt online.** Exponential moving average of feature statistics.
4. **Account for driving modes.** Idle, city, highway, cold start — each has
   its own normal range.

### 7.2 Contextual Ranges

A single global range per feature is insufficient. Ranges must be conditioned on
**driving context**:

```
Feature: volumetric_efficiency
  Context: idle        → normal: 0.28 - 0.35
  Context: city_cruise → normal: 0.55 - 0.75
  Context: highway     → normal: 0.70 - 0.90
  Context: cold_start  → normal: 0.20 - 0.50
```

### Principle: Normal Is Not a Single Number

A healthy engine behaves differently at idle vs highway vs cold start. The anomaly
detector must know **what mode the vehicle is in** before judging whether a feature
value is abnormal. Mode detection (idle / cruise / acceleration / deceleration /
cold start) is itself a derived feature and should be computed early in the pipeline.

---

## 8. Compute Architecture

### Principle: The Pi Computes Known Features. Everything Else Happens Elsewhere.

| Component | Runs On | Latency | Notes |
|---|---|---|---|
| CAN frame capture & decode | Pi | Real-time | Core data acquisition loop |
| Known feature computation | Pi | Real-time | Hardcoded derivation chains |
| Anomaly detection | Pi | Real-time | Compare features to calibrated ranges |
| Timeseries logging | Pi | Near real-time | Persistent storage of raw + derived |
| GP feature discovery | PC / Cloud | Batch | Operates on exported log dumps |
| Range calibration updates | Pi | Slow (online) | EMA update after each drive session |
| Feature promotion | Manual | N/A | Human reviews GP candidates |

---

## 9. Seed Features — Implement These First

These are well-established automotive diagnostic features. They are your
**Known Track foundation**.

| Feature | Derivation | Formula | Diagnoses |
|---|---|---|---|
| Warmup Rate | Level 1 | `Δcoolant_temp / Δt` (during cold start) | Thermostat stuck open/closed |
| Volumetric Efficiency | Level 1 | `MAF / (RPM × 0.5 × displacement)` | Intake leaks, clogged air filter |
| Total Fuel Trim | Level 1 | `LTFT + STFT` | O2 sensor drift, injector imbalance |
| Catalyst Efficiency | Level 1 | `std(O2_down, w) / std(O2_up, w)` | Catalytic converter degradation |
| Idle Stability | Level 1 | `std(RPM, 30s)` when `throttle ≈ 0` | Misfire, vacuum leak |
| Load-Normalised Temp | Level 2 | `(coolant - ambient) / engine_load` | Radiator / cooling system efficiency |
| Throttle-RPM Coherence | Level 2 | `correlation(Δthrottle, ΔRPM, 2s)` | Transmission slip, throttle body |
| Warmup Health | Level 2 | `warmup_rate / expected_rate(ambient)` | Thermostat + coolant system |

---

## 10. Summary of Non-Negotiable Rules

1. **Type-check every derived feature.** No dimensionally incoherent combinations.
2. **Penalise depth.** A deeper feature must prove it's better than a shallower one.
3. **Time is not optional.** Temporal operators are first-class, not afterthoughts.
4. **Known features ship first.** Discovery is a background process, not the product.
5. **Discovery runs offline.** Never run GP on the Pi's live compute budget.
6. **Promotions are manual.** No auto-deploying features that can't be interpreted.
7. **Normal ranges are contextual.** Mode-conditioned, not global.
8. **Score without labels.** Information-theoretic metrics, not supervised accuracy.
9. **Max derivation depth: 4.** Hard ceiling. Anything deeper is noise.
10. **Every feature has a registry entry.** No anonymous computations in the pipeline.

---

*This document is the source of truth for the KnightRider diagnostics engine.
Code that violates these principles is a bug, regardless of whether it compiles.*
