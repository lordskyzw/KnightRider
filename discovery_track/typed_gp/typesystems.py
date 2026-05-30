"""
Domain TypeSystem configs for the typed-GP core.

Each builder returns a [TypeSystem]. The operator list ORDER is significant: the
core derives its unary/binary lookup tables in this order, and `random.choice`
over the resulting candidate lists is what consumes the RNG — so to reproduce a
legacy run byte-for-byte, keep the ops in their original order.

`mechanical_ts()` is an exact replica of the original gp_engine.py tables
(verified by discovery_track/_regression_check.py). OBD and ECG configs port the
type systems embedded in experiments/run_obd_gp.py and real_world/run_mitbih.py.
"""
import numpy as np

from .core import TypeSystem, _rstd, _rmean


# ── Mechanical (pendulum / bearing) — Angle, Velocity, Accel, Energy, Scalar ──
def mechanical_ts() -> TypeSystem:
    TYPES = ('A', 'V', 'Ac', 'E', 'S')
    OPS = []
    # Unary temporal
    OPS.append(('ddt', 1, ('A',), 'V', lambda a, fs: np.gradient(a, 1 / fs)))
    OPS.append(('ddt', 1, ('V',), 'Ac', lambda a, fs: np.gradient(a, 1 / fs)))
    OPS.append(('ddt', 1, ('E',), 'E', lambda a, fs: np.gradient(a, 1 / fs)))
    # Unary trig (only on angles)
    OPS.append(('sin', 1, ('A',), 'S', lambda a, fs: np.sin(a)))
    OPS.append(('cos', 1, ('A',), 'S', lambda a, fs: np.cos(a)))
    # Unary shape
    OPS.append(('abs', 1, ('A',), 'A', lambda a, fs: np.abs(a)))
    OPS.append(('abs', 1, ('V',), 'V', lambda a, fs: np.abs(a)))
    OPS.append(('abs', 1, ('E',), 'E', lambda a, fs: np.abs(a)))
    OPS.append(('neg', 1, ('A',), 'A', lambda a, fs: -a))
    OPS.append(('neg', 1, ('V',), 'V', lambda a, fs: -a))
    OPS.append(('neg', 1, ('S',), 'S', lambda a, fs: -a))
    OPS.append(('square', 1, ('V',), 'E', lambda a, fs: a ** 2))
    OPS.append(('square', 1, ('A',), 'S', lambda a, fs: a ** 2))
    OPS.append(('square', 1, ('S',), 'S', lambda a, fs: a ** 2))
    # Unary rolling
    OPS.append(('rstd', 1, ('A',), 'A', lambda a, fs: _rstd(a, fs)))
    OPS.append(('rstd', 1, ('V',), 'V', lambda a, fs: _rstd(a, fs)))
    OPS.append(('rstd', 1, ('E',), 'E', lambda a, fs: _rstd(a, fs)))
    # Binary same-type add/sub (loop order = TYPES order)
    for t in TYPES:
        OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
        OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))
    # Binary products (typed)
    OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('A', 'V'), 'E', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'A'), 'A', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))
    # Binary ratios (typed)
    OPS.append(('div', 2, ('A', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('V', 'A'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('E', 'A'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('E', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('S', 'S'), 'S', lambda a, b, fs: a / (b + 1e-8)))

    TERMINALS = {'theta': 'A', 'omega': 'V'}
    CONSTS = [0.5, 1.0, 2.0, 9.81]
    return TypeSystem(types=TYPES, ops=OPS, terminals=TERMINALS, consts=CONSTS)
