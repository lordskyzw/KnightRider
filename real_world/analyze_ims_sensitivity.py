"""
IMS Sensitivity Analysis
========================
Re-analyze the 49 GP features from the IMS experiment with:
1. Multiple detection thresholds (2σ, 3σ, 4σ, 5σ)
2. CUSUM (cumulative sum) early detection
3. Drift analysis: feature distribution shift between early and mid-life

Goal: Can low-composite GP features detect degradation EARLIER than baselines?
"""
import numpy as np
from scipy import stats
from collections import Counter
from copy import deepcopy
import random, sys, os, time, warnings
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

np.random.seed(42)
random.seed(42)

# ── Import the full IMS machinery ───────────────────────────────────────────
# We need to reconstruct the GP trees. Fastest way: re-run the GP but save/load.
# Since we can't pickle lambdas, we re-run the evolution (same seed = same result).
# But that takes 2 hours. Instead: rebuild just what we need.

IMS_DIR = os.path.join(os.path.dirname(__file__), 'ims_data')
FS = 20000
SNAPSHOT_LEN = 20480
N_TRAIN = 100

def load_snapshot(filepath):
    try:
        data = np.loadtxt(filepath)
        if data.ndim == 1: data = data.reshape(-1, 1)
        return data
    except: return None

def snapshot_to_signals(snapshot):
    n_cols = snapshot.shape[1] if snapshot.ndim > 1 else 1
    signals = {}
    if n_cols >= 1: signals['B1'] = snapshot[:, 0] if n_cols > 1 else snapshot.flatten()
    if n_cols >= 2: signals['B2'] = snapshot[:, 1]
    if n_cols >= 3: signals['B3'] = snapshot[:, 2]
    if n_cols >= 4: signals['B4'] = snapshot[:, 3]
    for k in ['B1', 'B2', 'B3', 'B4']:
        if k not in signals: signals[k] = np.zeros(SNAPSHOT_LEN)
    return signals

# ── Baseline features ──────────────────────────────────────────────────────
def extract_baseline(signals):
    f = {}
    for bname in ['B1', 'B2', 'B3', 'B4']:
        if bname not in signals: continue
        s = signals[bname]
        f[f'{bname}_rms']   = np.sqrt(np.mean(s**2))
        f[f'{bname}_std']   = np.std(s)
        f[f'{bname}_kurt']  = float(stats.kurtosis(s, fisher=True))
        f[f'{bname}_crest'] = np.max(np.abs(s)) / (np.sqrt(np.mean(s**2)) + 1e-8)
        f[f'{bname}_skew']  = float(stats.skew(s))
        f[f'{bname}_p2p']   = np.max(s) - np.min(s)
        f[f'{bname}_energy'] = np.sum(s**2)
    if 'B1' in signals and 'B2' in signals:
        f['B1B2_ratio'] = np.sqrt(np.mean(signals['B1']**2)) / (np.sqrt(np.mean(signals['B2']**2)) + 1e-8)
    if 'B1' in signals and 'B3' in signals:
        f['B1B3_ratio'] = np.sqrt(np.mean(signals['B1']**2)) / (np.sqrt(np.mean(signals['B3']**2)) + 1e-8)
    return f

# ── Detection functions ────────────────────────────────────────────────────
def find_first_detection(timeline, h_mu, h_sig, threshold, min_consec=3):
    if h_sig < 1e-10: h_sig = 1e-10
    z = np.abs(timeline - h_mu) / h_sig
    count = 0
    for i, zi in enumerate(z):
        if np.isfinite(zi) and zi > threshold:
            count += 1
            if count >= min_consec:
                return i - min_consec + 1
        else:
            count = 0
    return len(timeline)

def cusum_detection(timeline, h_mu, h_sig, cusum_threshold=10.0, drift=0.5):
    """CUSUM (Cumulative Sum) detection — more sensitive to gradual drift."""
    if h_sig < 1e-10: h_sig = 1e-10
    z = (timeline - h_mu) / h_sig
    s_pos = 0.0
    s_neg = 0.0
    for i, zi in enumerate(z):
        if not np.isfinite(zi): continue
        s_pos = max(0, s_pos + zi - drift)
        s_neg = max(0, s_neg - zi - drift)
        if s_pos > cusum_threshold or s_neg > cusum_threshold:
            return i
    return len(timeline)

def ewma_detection(timeline, h_mu, h_sig, lam=0.1, L=3.0):
    """EWMA (Exponentially Weighted Moving Average) control chart."""
    if h_sig < 1e-10: h_sig = 1e-10
    z = np.zeros(len(timeline))
    z[0] = h_mu
    for i in range(1, len(timeline)):
        if np.isfinite(timeline[i]):
            z[i] = lam * timeline[i] + (1 - lam) * z[i-1]
        else:
            z[i] = z[i-1]
    # Control limits widen then stabilize
    for i in range(len(z)):
        sigma_z = h_sig * np.sqrt(lam / (2 - lam) * (1 - (1-lam)**(2*(i+1))))
        if abs(z[i] - h_mu) > L * sigma_z:
            return i
    return len(timeline)

# ── GP feature formulas (hardcoded from the run) ──────────────────────────
# Instead of re-running GP, we compute the key features directly from formulas
# discovered in the previous run. These are the most interesting ones.

def _rstd(a, w=200):
    return pd.Series(a).rolling(w, min_periods=w//2).std().fillna(0).values

def gp_feature_ddt_ddt_B1(signals):
    """ddt(ddt(B1)) — jerk, the #41 feature that detected earliest"""
    return np.gradient(np.gradient(signals['B1'], 1/FS), 1/FS)

def gp_feature_neg2_ddt_B1(signals):
    """neg(2.0) * ddt(B1) — scaled acceleration, #42"""
    return -2.0 * np.gradient(signals['B1'], 1/FS)

def gp_feature_neg_env_ddt_B1_add_B1(signals):
    """neg(env(ddt(B1))) + B1 — envelope-subtracted, #29"""
    return -np.abs(np.gradient(signals['B1'], 1/FS)) + signals['B1']

def gp_feature_env_env_B1(signals):
    """env(env(B1)) = abs(abs(B1)) = abs(B1), #34"""
    return np.abs(signals['B1'])

def gp_feature_B1(signals):
    """Just B1 raw, #35"""
    return signals['B1'].copy()

def gp_feature_2_B1_sub_B3_add_B3(signals):
    """2*(B1-B3)+B3 = 2*B1-B3, #40"""
    return 2.0 * signals['B1'] - signals['B3']

def gp_feature_B2_neg_B1(signals):
    """B2 + neg(B1) = B2 - B1, #46"""
    return signals['B2'] - signals['B1']

def gp_feature_env_B3_add_B1(signals):
    """env(B3) + B1 = abs(B3) + B1, #21"""
    return np.abs(signals['B3']) + signals['B1']

def gp_feature_env_B1_sub_B3(signals):
    """env(env(B1-B3)) = abs(B1-B3), #30"""
    return np.abs(signals['B1'] - signals['B3'])

def gp_feature_B1_sub_B3_B2_rstd_B4(signals):
    """B1 - (B3 - (B2 - rstd(B4))), #38"""
    return signals['B1'] - (signals['B3'] - (signals['B2'] - _rstd(signals['B4'])))

def gp_feature_ddt_B1(signals):
    """ddt(B1) — first derivative / acceleration"""
    return np.gradient(signals['B1'], 1/FS)

def gp_feature_rstd_B1(signals):
    """rstd(B1) — rolling std"""
    return _rstd(signals['B1'])

def gp_feature_B1_div_B3(signals):
    """B1 / B3 — cross-bearing ratio"""
    return signals['B1'] / (signals['B3'] + 1e-8)

GP_FEATURES = {
    'ddt(ddt(B1))':         (gp_feature_ddt_ddt_B1, 'kurt'),
    'neg(2)*ddt(B1)':       (gp_feature_neg2_ddt_B1, 'std'),
    '-env(ddt(B1))+B1':     (gp_feature_neg_env_ddt_B1_add_B1, 'std'),
    'abs(B1)':              (gp_feature_env_env_B1, 'rms'),
    'B1':                   (gp_feature_B1, 'rms'),
    '2*B1-B3':              (gp_feature_2_B1_sub_B3_add_B3, 'std'),
    'B2-B1':                (gp_feature_B2_neg_B1, 'std'),
    'abs(B3)+B1':           (gp_feature_env_B3_add_B1, 'rms'),
    'abs(B1-B3)':           (gp_feature_env_B1_sub_B3, 'rms'),
    'B1-B3+B2-rstd(B4)':   (gp_feature_B1_sub_B3_B2_rstd_B4, 'std'),
    'ddt(B1)':              (gp_feature_ddt_B1, 'std'),
    'rstd(B1)':             (gp_feature_rstd_B1, 'rms'),
    'B1/B3':                (gp_feature_B1_div_B3, 'std'),
}

SUMMARIES = {
    'std': lambda a: np.std(a),
    'rms': lambda a: np.sqrt(np.mean(a**2)),
    'kurt': lambda a: float(stats.kurtosis(a, fisher=True)) if len(a) > 10 else 0.0,
}

# ── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':

    print("=" * 80)
    print("  IMS SENSITIVITY ANALYSIS")
    print("  Can low-composite GP features detect degradation earlier?")
    print("=" * 80)

    # ── Load data ───────────────────────────────────────────────────────────
    print("\n[1/4] Loading IMS data...", flush=True)
    test_dir = os.path.join(IMS_DIR, '2nd_test')
    files = sorted(os.listdir(test_dir))
    snapshots = []
    for f in files:
        path = os.path.join(test_dir, f)
        if os.path.isdir(path): continue
        s = load_snapshot(path)
        if s is not None and len(s) >= 1000:
            snapshots.append(s)
    all_signals = [snapshot_to_signals(s) for s in snapshots]
    n_total = len(all_signals)
    print(f"  {n_total} snapshots loaded")

    # ── Compute all timelines ───────────────────────────────────────────────
    print("\n[2/4] Computing feature timelines...", flush=True)

    # Baselines
    bl_df = pd.DataFrame([extract_baseline(s) for s in all_signals])
    bl_feats = [c for c in bl_df.columns if c.startswith('B1') or 'ratio' in c]

    # GP features
    gp_timelines = {}
    for name, (func, smry_name) in GP_FEATURES.items():
        sfunc = SUMMARIES[smry_name]
        tl = []
        for signals in all_signals:
            try:
                val = func(signals)
                finite = val[np.isfinite(val)]
                tl.append(sfunc(finite) if len(finite) > 100 else np.nan)
            except:
                tl.append(np.nan)
        gp_timelines[name] = np.array(tl)

    print(f"  {len(bl_feats)} baseline + {len(gp_timelines)} GP features")

    # ── Multi-threshold analysis ────────────────────────────────────────────
    print("\n[3/4] Multi-threshold detection analysis...", flush=True)

    thresholds = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    print(f"\n  BASELINE DETECTION (snapshot / {n_total}):")
    print(f"  {'Feature':<15}", end='')
    for th in thresholds:
        print(f"  {th:.1f}s", end='')
    print(f"  {'CUSUM':>7}  {'EWMA':>7}")
    print("  " + "-" * (15 + 7*len(thresholds) + 16))

    bl_results = {}
    for feat in bl_feats:
        tl = bl_df[feat].values
        h_mu = np.nanmean(tl[:N_TRAIN])
        h_sig = np.nanstd(tl[:N_TRAIN])
        row = {}
        for th in thresholds:
            row[f'z{th}'] = find_first_detection(tl, h_mu, h_sig, th, min_consec=3)
        row['cusum'] = cusum_detection(tl, h_mu, h_sig, cusum_threshold=8.0, drift=0.5)
        row['ewma'] = ewma_detection(tl, h_mu, h_sig, lam=0.1, L=3.0)
        bl_results[feat] = row

        print(f"  {feat:<15}", end='')
        for th in thresholds:
            v = row[f'z{th}']
            s = f"{v}" if v < n_total else "---"
            print(f"  {s:>5}", end='')
        for method in ['cusum', 'ewma']:
            v = row[method]
            s = f"{v}" if v < n_total else "---"
            print(f"  {s:>7}", end='')
        print()

    # Best baseline per method
    print(f"\n  BEST BASELINE PER METHOD:")
    for method_key in [f'z{th}' for th in thresholds] + ['cusum', 'ewma']:
        best_feat = min(bl_results, key=lambda f: bl_results[f][method_key])
        best_val = bl_results[best_feat][method_key]
        pct = best_val / n_total * 100 if best_val < n_total else float('inf')
        label = method_key.replace('z', '') + 'sigma' if method_key.startswith('z') else method_key.upper()
        print(f"  {label:<10}: {best_feat:<15} at snap {best_val:>5} ({pct:5.1f}%)")

    print(f"\n  GP FEATURE DETECTION (snapshot / {n_total}):")
    print(f"  {'Feature':<22}", end='')
    for th in thresholds:
        print(f"  {th:.1f}s", end='')
    print(f"  {'CUSUM':>7}  {'EWMA':>7}")
    print("  " + "-" * (22 + 7*len(thresholds) + 16))

    gp_results = {}
    for name, tl in gp_timelines.items():
        h_mu = np.nanmean(tl[:N_TRAIN])
        h_sig = np.nanstd(tl[:N_TRAIN])
        row = {}
        for th in thresholds:
            row[f'z{th}'] = find_first_detection(tl, h_mu, h_sig, th, min_consec=3)
        row['cusum'] = cusum_detection(tl, h_mu, h_sig, cusum_threshold=8.0, drift=0.5)
        row['ewma'] = ewma_detection(tl, h_mu, h_sig, lam=0.1, L=3.0)
        gp_results[name] = row

        print(f"  {name:<22}", end='')
        for th in thresholds:
            v = row[f'z{th}']
            s = f"{v}" if v < n_total else "---"
            print(f"  {s:>5}", end='')
        for method in ['cusum', 'ewma']:
            v = row[method]
            s = f"{v}" if v < n_total else "---"
            print(f"  {s:>7}", end='')
        print()

    # ── Head-to-head at every threshold ─────────────────────────────────────
    print(f"\n[4/4] HEAD-TO-HEAD COMPARISON")
    print("=" * 80)

    all_methods = [f'z{th}' for th in thresholds] + ['cusum', 'ewma']

    print(f"\n  {'Method':<12} {'Best GP':<22} {'GP snap':>7} {'Best BL':<15} {'BL snap':>7} {'Lead':>8} {'Winner':>7}")
    print("  " + "-" * 82)

    gp_wins = 0; bl_wins = 0; ties = 0
    for method_key in all_methods:
        best_gp_name = min(gp_results, key=lambda f: gp_results[f][method_key])
        best_gp_val = gp_results[best_gp_name][method_key]

        best_bl_name = min(bl_results, key=lambda f: bl_results[f][method_key])
        best_bl_val = bl_results[best_bl_name][method_key]

        lead = best_bl_val - best_gp_val
        lead_hrs = abs(lead) * 10 / 60

        label = method_key.replace('z', '') + 'sigma' if method_key.startswith('z') else method_key.upper()

        if best_gp_val < best_bl_val:
            winner = "GP"; gp_wins += 1
            lead_str = f"+{lead_hrs:.1f}h"
        elif best_bl_val < best_gp_val:
            winner = "BL"; bl_wins += 1
            lead_str = f"-{lead_hrs:.1f}h"
        else:
            winner = "TIE"; ties += 1
            lead_str = "0h"

        print(f"  {label:<12} {best_gp_name:<22} {best_gp_val:>7} "
              f"{best_bl_name:<15} {best_bl_val:>7} {lead_str:>8} {winner:>7}")

    print(f"\n  SCORE: GP {gp_wins} | TIE {ties} | Baseline {bl_wins}")

    # ── Drift analysis ──────────────────────────────────────────────────────
    print(f"\n  DRIFT ANALYSIS: feature shift between early and mid-life")
    print(f"  Early = snapshots 0-{N_TRAIN}, Mid = snapshots 300-500 (before any detection)")
    print()

    print(f"  {'Feature':<22} {'Early mu':>10} {'Mid mu':>10} {'Drift sigma':>12} {'Interpret'}")
    print("  " + "-" * 70)

    for name, tl in gp_timelines.items():
        early = tl[:N_TRAIN]
        mid = tl[300:500]
        e_mu = np.nanmean(early)
        m_mu = np.nanmean(mid)
        e_sig = np.nanstd(early)
        drift_sigma = (m_mu - e_mu) / (e_sig + 1e-10)
        interp = "***DRIFT***" if abs(drift_sigma) > 2.0 else "drift" if abs(drift_sigma) > 1.0 else "stable"
        print(f"  {name:<22} {e_mu:>10.4f} {m_mu:>10.4f} {drift_sigma:>+12.2f}  {interp}")

    print(f"\n  BASELINE DRIFT:")
    for feat in bl_feats:
        tl = bl_df[feat].values
        early = tl[:N_TRAIN]
        mid = tl[300:500]
        e_mu = np.nanmean(early)
        m_mu = np.nanmean(mid)
        e_sig = np.nanstd(early)
        drift_sigma = (m_mu - e_mu) / (e_sig + 1e-10)
        interp = "***DRIFT***" if abs(drift_sigma) > 2.0 else "drift" if abs(drift_sigma) > 1.0 else "stable"
        print(f"  {feat:<22} {e_mu:>10.4f} {m_mu:>10.4f} {drift_sigma:>+12.2f}  {interp}")

    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    print(f"  GP wins {gp_wins}/{len(all_methods)} detection methods")
    print(f"  Ties: {ties}, Baseline wins: {bl_wins}")
    # Find the single biggest GP lead
    biggest_lead = 0
    biggest_method = ""
    for method_key in all_methods:
        best_gp = min(gp_results[f][method_key] for f in gp_results)
        best_bl = min(bl_results[f][method_key] for f in bl_results)
        lead = best_bl - best_gp
        if lead > biggest_lead:
            biggest_lead = lead
            biggest_method = method_key
    if biggest_lead > 0:
        print(f"  Biggest GP lead: {biggest_lead} snapshots = {biggest_lead*10/60:.1f} hours "
              f"(method: {biggest_method})")
    print("=" * 80)
