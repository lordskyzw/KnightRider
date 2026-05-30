import random, numpy as np
from typed_gp import load_knight_rider_obd, obd_ts, core

random.seed(7); np.random.seed(7)
csv = r"C:\Users\Hp 830 G5\KnightRider\captures\axio-only-20260529.wide.csv"
trials, terminals, fs = load_knight_rider_obd(csv, fs_hz=2.0, n_windows=18)
print(f"loaded {len(trials)} windows | fs={fs}Hz | terminals present: {terminals}")
print(f"window length (samples): {len(next(iter(trials[0].values())))}")
ts = obd_ts(terminals=terminals)
print(f"obd_ts: {len(ts.types)} types, {len(ts.terminals)} terminals, {len(ts.ops)} ops")
paired = core.run_gp(ts, trials, fs=fs, pop_size=60, generations=12, max_depth=4, verbose=False)
print("\nTop 6 evolved features (fitness | expression):")
for f, t in paired[:6]:
    print(f"  {f:.4f}  {t.expr_str()[:70]}")
