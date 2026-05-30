"""
Regression guard for the typed-GP consolidation.

Runs the engine on a FIXED seed + fixed synthetic healthy trials and prints the
sorted (fitness, expression) population. The mechanical path must produce
byte-identical output before and after the refactor — that's the proof the
consolidation didn't drift the published behaviour.

Usage:
    python _regression_check.py            # prints the fingerprint
    python _regression_check.py > GOLDEN   # capture, then diff after refactor

It imports `gp_engine` (the module the paper's discovery runs use). Before the
refactor that's the original; after, it's the thin shim over typed_gp — same
output either way if the consolidation is faithful.
"""
import random
import numpy as np

import gp_engine as E


def make_trials(n=18, steps=400, fs=50.0, seed=0):
    """Deterministic synthetic pendulum-like healthy trials (theta, omega)."""
    rng = np.random.RandomState(seed)
    trials = []
    t = np.arange(steps) / fs
    for i in range(n):
        w0 = 2.0 + 0.05 * i
        damp = 0.02 + 0.001 * i
        theta = np.exp(-damp * t) * np.cos(w0 * t) + 0.01 * rng.randn(steps)
        omega = np.gradient(theta, 1 / fs)
        trials.append({'theta': theta, 'omega': omega})
    return trials


def fingerprint():
    random.seed(12345)
    np.random.seed(12345)
    trials = make_trials()
    paired = E.run_gp(
        trials, fs=50.0,
        pop_size=60, generations=12, max_depth=4,
        tournament_k=5, elite_frac=0.05,
        crossover_rate=0.7, mutation_rate=0.25, verbose=False,
    )
    lines = []
    for fit, tree in paired:
        lines.append(f"{fit:.6f}\t{tree.expr_str()}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(fingerprint())
