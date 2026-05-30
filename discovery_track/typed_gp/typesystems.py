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


# ── Vectorized rolling (OBD/ECG authored their own; preserved verbatim) ──────
def _vec_rstd(a, fs, w):
    n = len(a)
    if n < w:
        return np.zeros(n)
    cs = np.concatenate([[0], np.cumsum(a)])
    cs2 = np.concatenate([[0], np.cumsum(a ** 2)])
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = np.maximum(0, s2 / w - (s / w) ** 2)
    out = np.zeros(n)
    out[w - 1:] = np.sqrt(var)
    return out


def _obd_rmean(a, fs, w=30):
    n = len(a)
    if n < w:
        return a.copy()
    cs = np.concatenate([[0], np.cumsum(a)])
    rm = (cs[w:] - cs[:-w]) / w
    out = np.zeros(n)
    out[w - 1:] = rm
    out[:w - 1] = np.cumsum(a[:w - 1]) / np.arange(1, w)
    return out


def _ecg_rmean(a, fs, w=25):
    n = len(a)
    if n < w:
        return a.copy()
    cs = np.concatenate([[0], np.cumsum(a)])
    out = np.zeros(n)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


# ── Automotive (OBD-II) — Rotation, Temp, Flow, Position, Ratio, Scalar ──────
# Full terminal map (the signals the GP can draw on). `terminals` can be a subset
# (the loader passes whatever the capture actually contains — e.g. no upstream O2
# until we add PID 0x14), in which case absent signals simply aren't offered.
OBD_TERMINALS = {
    'rpm': 'R', 'coolant': 'T', 'iat': 'T', 'maf': 'F', 'throttle': 'P',
    'stft': 'Ra', 'ltft': 'Ra', 'o2_up': 'Ra', 'o2_down': 'Ra',
}


def obd_ts(terminals=None, rstd_window=30) -> TypeSystem:
    TYPES = ('R', 'T', 'F', 'P', 'Ra', 'S')
    w = rstd_window
    OPS = []
    for t in ('R', 'T', 'F', 'P', 'Ra'):
        OPS.append(('ddt', 1, (t,), t, lambda a, fs: np.gradient(a, 1 / fs)))
    for t in ('R', 'T', 'F', 'P', 'Ra'):
        OPS.append(('rstd', 1, (t,), t, lambda a, fs: _vec_rstd(a, fs, w)))
    for t in ('R', 'T', 'F', 'P', 'Ra'):
        OPS.append(('rmean', 1, (t,), t, lambda a, fs: _obd_rmean(a, fs, w)))
    for t in ('R', 'T', 'F', 'P', 'Ra', 'S'):
        OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))
    for t in TYPES:
        OPS.append(('neg', 1, (t,), t, lambda a, fs: -a))
    for t in TYPES:
        OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
        OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))
    # Cross-type ratios (the diagnostic money operators)
    OPS.append(('div', 2, ('F', 'R'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('R', 'F'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('T', 'T'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('F', 'P'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
    for t in TYPES:
        if t != 'S':
            OPS.append(('div', 2, (t, t), 'S', lambda a, b, fs: a / (b + 1e-8)))
    for t in TYPES:
        OPS.append(('mul', 2, ('S', t), t, lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('P', 'R'), 'F', lambda a, b, fs: a * b))
    OPS.append(('sub_cross', 2, ('T', 'T'), 'S', lambda a, b, fs: a - b))
    OPS.append(('sub_cross', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a - b))

    terms = dict(OBD_TERMINALS) if terminals is None else dict(terminals)
    CONSTS = [0.5, 1.0, 2.0, 10.0]
    return TypeSystem(types=TYPES, ops=OPS, terminals=terms, consts=CONSTS)


# ── Physiological (ECG / MIT-BIH) — Voltage, Energy, Scalar ──────────────────
def ecg_ts(terminals=None, rstd_window=25) -> TypeSystem:
    TYPES = ('V', 'E', 'S')
    w = rstd_window
    OPS = []
    OPS.append(('ddt', 1, ('V',), 'V', lambda a, fs: np.gradient(a, 1.0 / fs)))
    OPS.append(('ddt', 1, ('E',), 'E', lambda a, fs: np.gradient(a, 1.0 / fs)))
    for t in TYPES:
        OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))
    OPS.append(('neg', 1, ('V',), 'V', lambda a, fs: -a))
    OPS.append(('neg', 1, ('S',), 'S', lambda a, fs: -a))
    OPS.append(('square', 1, ('V',), 'E', lambda a, fs: a ** 2))
    OPS.append(('square', 1, ('E',), 'E', lambda a, fs: np.clip(a ** 2, 0, 1e12)))
    OPS.append(('square', 1, ('S',), 'S', lambda a, fs: a ** 2))
    OPS.append(('rstd', 1, ('V',), 'V', lambda a, fs: _vec_rstd(a, fs, w)))
    OPS.append(('rstd', 1, ('E',), 'E', lambda a, fs: _vec_rstd(a, fs, w)))
    OPS.append(('rmean', 1, ('V',), 'V', lambda a, fs: _ecg_rmean(a, fs, w)))
    OPS.append(('rmean', 1, ('E',), 'E', lambda a, fs: _ecg_rmean(a, fs, w)))
    for t in TYPES:
        OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
        OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))
    OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
    OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))
    OPS.append(('div', 2, ('V', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
    OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))

    terms = {'ECG1': 'V', 'ECG2': 'V'} if terminals is None else dict(terminals)
    CONSTS = [0.5, 1.0, 2.0, 5.0]
    return TypeSystem(types=TYPES, ops=OPS, terminals=terms, consts=CONSTS)
