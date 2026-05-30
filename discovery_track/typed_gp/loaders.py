"""
Real Knight Rider capture → typed-GP trials.

Bridges the field data (`captures/*.wide.csv`, produced by
`captures/decode_buffer.py`) into the shape the GP consumes: a list of "trials",
each a dict {terminal_name: np.ndarray} on a fixed, dense time grid.

The wide.csv is sparse/async (one signal per source row), so we resample to a
fixed rate and forward-fill. A single continuous session is split into
contiguous windows — each window is one "trial", giving the unsupervised fitness
(tightness / stationarity across trials) a distribution to score against.

This is the piece `experiments/stage2_obd.py:37` was waiting for ("Replace with
real CSV loading when Pi logs are available").
"""
import numpy as np
import pandas as pd

# Knight Rider wire signal → OBD TypeSystem terminal (see typesystems.OBD_TERMINALS).
# Upstream O2 (o2_up / B1S1) is intentionally listed but NOT yet captured by the
# poller — the loader drops absent signals, so it just won't be offered until we
# add PID 0x14. Catalyst-efficiency features need it (P0420), hence the protocol.
KR_SIGNAL_TO_OBD = {
    'obd.rpm':             'rpm',
    'obd.coolant_temp':    'coolant',
    'obd.intake_air_temp': 'iat',
    'obd.maf':             'maf',
    'obd.throttle':        'throttle',
    'obd.stft_b1':         'stft',
    'obd.ltft_b1':         'ltft',
    'obd.o2_b1s1_v':       'o2_up',    # not captured yet (add PID 0x14)
    'obd.o2_b1s2_v':       'o2_down',
}


def _base_signal(col: str) -> str:
    """'obd.rpm [rpm]' -> 'obd.rpm'."""
    return col.split(' [', 1)[0].strip()


def load_knight_rider_obd(csv_path, fs_hz=2.0, n_windows=20, min_window_len=60):
    """Load a Knight Rider wide.csv into (trials, terminals, fs).

    * ``fs_hz``     — resample rate; the GP's `fs` is returned to match.
    * ``n_windows`` — split the session into this many contiguous trials.
    * Returns ``(trials, terminals, fs_hz)`` where ``terminals`` is the OBD
      terminal→type subset actually present (feed it to ``obd_ts(terminals=…)``).

    Signals absent from the capture are dropped (e.g. ``o2_up`` until PID 0x14).
    """
    df = pd.read_csv(csv_path)
    # Identify the timestamp column + map data columns to OBD terminals.
    ts_col = df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True, errors='coerce')
    df = df.dropna(subset=[ts_col]).set_index(ts_col).sort_index()

    col_for_term = {}
    for col in df.columns:
        term = KR_SIGNAL_TO_OBD.get(_base_signal(col))
        if term is not None:
            col_for_term[term] = col

    if not col_for_term:
        raise ValueError(f"No OBD-mappable signals in {csv_path}")

    # Resample to a fixed grid and forward/back-fill the sparse columns.
    period = pd.to_timedelta(1.0 / fs_hz, unit='s')
    sub = df[[c for c in col_for_term.values()]].apply(pd.to_numeric, errors='coerce')
    grid = sub.resample(period).mean().ffill().bfill()
    grid = grid.dropna(axis=1, how='all')

    # Keep only terminals that survived (had any data).
    from .typesystems import OBD_TERMINALS
    terminals = {term: OBD_TERMINALS[term]
                 for term, col in col_for_term.items() if col in grid.columns}

    arrs = {term: grid[col].to_numpy(dtype=float) for term, col in col_for_term.items()
            if col in grid.columns}
    n = len(grid)
    if n < n_windows * min_window_len:
        # Too short to window finely — fall back to as many windows as fit.
        n_windows = max(1, n // min_window_len)

    bounds = np.linspace(0, n, n_windows + 1, dtype=int)
    trials = []
    for i in range(n_windows):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < min_window_len:
            continue
        trials.append({term: arr[lo:hi].copy() for term, arr in arrs.items()})

    return trials, terminals, fs_hz
