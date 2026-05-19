"""
Discovery Track — FRAMEWORK PROPOSAL
=====================================
The composite (tightness + stationarity + headroom) measures distribution
SHAPE but not information CONTENT. It can't distinguish "tight because
physics" from "tight because noise-stable." GP features ranked #36-37
despite AUC=0.87 because their healthy-data distributions are noisier.

FIX: Add SPLIT-HALF RELIABILITY to the composite.

Split-half reliability:
  1. Cut each 25s trial in half (0-12.5s, 12.5-25s)
  2. Compute the feature on each half
  3. Correlate first-half values with second-half values across 60 trials
  4. High corr = feature captures stable physics (same system, same feature)
  5. Low corr = feature amplifies noise (different noise in each half)

New composite = (tightness + stationarity + headroom + reliability) / 4
No depth penalty needed — reliability naturally penalizes noise amplifiers.

This is fully unsupervised, requires no fault labels, and is deployable.
"""
import numpy as np
import pandas as pd
from scipy.integrate import odeint
from scipy import stats
from scipy.signal import find_peaks
from sklearn.metrics import roc_auc_score
import warnings, time
warnings.filterwarnings('ignore')

np.random.seed(7)

# ── Pendulum ─────────────────────────────────────────────────────────────────
def pendulum_ode(s, t, g, L, b, Fa, Ff):
    th, om = s
    return [om, -(g/L)*np.sin(th) - b*om + Fa*np.sin(Ff*t)]

def simulate(T=25.0, fs=200.0, g=9.81, L=1.0, b=0.3,
             th0=None, Fa=0.0, Ff=2.05, lag=0, noise=0.006):
    if th0 is None:
        th0 = np.pi/4 + np.random.uniform(-0.08, 0.08)
    t = np.arange(0, T, 1/fs)
    sol = odeint(pendulum_ode, [th0, 0.0], t, args=(g, L, b, Fa, Ff))
    th, om = sol[:,0].copy(), sol[:,1].copy()
    if lag > 0:
        th = np.concatenate([np.full(lag, th[0]), th[:-lag]])
    th += np.random.normal(0, noise, len(t))
    om += np.random.normal(0, noise * 0.5, len(t))
    return dict(t=t, theta=th, omega=om, g=g, L=L, fs=fs)

FAULT_RANGES = {
    'high_damp': {'param': 'b',  'healthy': 0.30, 'full': 0.90},
    'short_L':   {'param': 'L',  'healthy': 1.00, 'full': 0.55},
    'forcing':   {'param': 'Fa', 'healthy': 0.00, 'full': 1.30},
}
SEVERITIES = [0.0, 0.25, 0.5, 0.75, 1.0]
N_HEALTHY  = 60
N_HELD_OUT = 20
N_FAULT    = 40

# ── Helpers ──────────────────────────────────────────────────────────────────
def safe_ent(arr, bins=30):
    v = arr[np.isfinite(arr)]
    if len(v) < 10: return np.nan
    c, _ = np.histogram(v, bins=bins)
    p = c / c.sum(); p = p[p > 0]
    return -np.sum(p * np.log2(p))

def safe_kurt(arr):
    v = arr[np.isfinite(arr)]
    return float(stats.kurtosis(v, fisher=True)) if len(v) > 4 else np.nan

def safe_skew(arr):
    v = arr[np.isfinite(arr)]
    return float(stats.skew(v)) if len(v) > 4 else np.nan

def rolling_scalar(arr, w, fn=np.std):
    s = pd.Series(arr).rolling(w, min_periods=w // 2)
    return s.std().dropna().mean() if fn == np.std else s.mean().dropna().mean()

def fit_decay(t, th):
    pk_idx, _ = find_peaks(th, height=0.01)
    if len(pk_idx) < 5: return np.nan
    tp, yp = t[pk_idx], np.abs(th[pk_idx])
    yp = np.clip(yp, 1e-6, None)
    try:
        sl, _, r, _, _ = stats.linregress(tp, np.log(yp))
        return -sl if r**2 > 0.6 else np.nan
    except:
        return np.nan

def phase_area(th, om, w=300):
    areas = []
    for i in range(w, len(th), w // 2):
        x, y = th[i-w:i], om[i-w:i]
        areas.append(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
    return np.mean(areas) if areas else np.nan

def crossings(arr):
    mn = arr.mean()
    return float(np.sum(np.diff(np.sign(arr - mn)) != 0)) / len(arr)

# ── Feature Extraction ───────────────────────────────────────────────────────
def extract(run):
    t, th, om = run['t'], run['theta'], run['omega']
    g, L, fs  = run['g'], run['L'], run['fs']
    eps = 1e-8
    W20 = int(0.20 * fs); W50 = int(0.50 * fs); W100 = int(1.0 * fs)

    f = {}
    f['L0_A_std']   = np.std(th)
    f['L0_A_ent']   = safe_ent(th)
    f['L0_A_kurt']  = safe_kurt(th)
    f['L0_A_skew']  = safe_skew(th)
    f['L0_A_cross'] = crossings(th)
    f['L0_V_std']   = np.std(om)
    f['L0_V_ent']   = safe_ent(om)
    f['L0_V_kurt']  = safe_kurt(om)
    f['L0_V_skew']  = safe_skew(om)

    dth = np.gradient(th, 1 / fs)
    f['L1_dA_std']  = np.std(dth)
    f['L1_dA_ent']  = safe_ent(dth)
    f['L1_dA_kurt'] = safe_kurt(dth)
    dom = np.gradient(om, 1 / fs)
    f['L1_dV_std']  = np.std(dom)
    f['L1_dV_ent']  = safe_ent(dom)
    f['L1_dV_kurt'] = safe_kurt(dom)

    E = 0.5 * om**2 + (g / L) * (1 - np.cos(th))
    f['L1_E_mean']  = np.mean(E)
    f['L1_E_std']   = np.std(E)
    f['L1_E_ent']   = safe_ent(E)
    f['L1_E_kurt']  = safe_kurt(E)
    f['L1_E_skew']  = safe_skew(E)

    tau = th / (om + eps)
    tau_c = tau[np.abs(tau) < 50]
    f['L1_T_std']   = np.nanstd(tau_c)
    f['L1_T_ent']   = safe_ent(tau_c)
    f['L1_A_rstd20']  = rolling_scalar(th, W20)
    f['L1_V_rstd20']  = rolling_scalar(om, W20)
    f['L1_E_rstd50']  = rolling_scalar(E, W50)
    f['L1_decay']      = fit_decay(t, th)
    f['L1_phase_area'] = phase_area(th, om)
    f['L1_A_range']    = np.percentile(th, 95) - np.percentile(th, 5)
    f['L1_V_range']    = np.percentile(om, 95) - np.percentile(om, 5)

    dE = np.gradient(E, 1 / fs)
    f['L2_dE_std']  = np.std(dE)
    f['L2_dE_ent']  = safe_ent(dE)
    f['L2_dE_kurt'] = safe_kurt(dE)
    f['L2_dE_skew'] = safe_skew(dE)
    dtau = np.gradient(np.where(np.abs(tau) < 50, tau, np.nan), 1 / fs)
    f['L2_dT_std']      = np.nanstd(dtau[np.isfinite(dtau)])
    f['L2_dA_rstd50']   = rolling_scalar(dth, W50)
    f['L2_E_rstd100']   = rolling_scalar(E, W100)
    f['L2_E_per_A']     = np.std(E) / (np.std(th) + eps)
    f['L2_V_per_E']     = np.std(om) / (np.std(E) + eps)
    n = min(len(dth), len(dE))
    f['L2_dA_dE_corr']  = float(np.corrcoef(dth[:n], dE[:n])[0, 1])
    dr = f['L1_decay']
    f['L2_decay_x_range'] = dr * f['L1_A_range'] if np.isfinite(dr) else np.nan
    f['L2_phase_per_E']   = f['L1_phase_area'] / (np.mean(E) + eps)
    f['L2_dA_cross']      = crossings(dth)

    # GP features
    gp1 = np.gradient(om**2, 1 / fs)
    f['L2_GP_power']  = np.std(gp1)
    rstd_th = pd.Series(th).rolling(W20, min_periods=W20 // 2).std().fillna(0).values
    f['L2_GP_ampvel'] = np.std(rstd_th * om)

    return f

def feat_level(name):
    return int(name[1])

# ── Split-half: cut trial at midpoint, return two sub-trials ─────────────────
def split_trial(run):
    n = len(run['t'])
    mid = n // 2
    first = {k: v[:mid] if isinstance(v, np.ndarray) else v
             for k, v in run.items()}
    second = {k: v[mid:] if isinstance(v, np.ndarray) else v
              for k, v in run.items()}
    return first, second

# ── Scoring Functions ────────────────────────────────────────────────────────
def composite_OLD(series, level):
    """Original composite: tightness + stationarity + headroom - depth_penalty."""
    vals = series.dropna().values
    if len(vals) < 15: return np.nan
    c, _ = np.histogram(vals, bins=min(25, len(vals) // 3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) /
                          (abs(np.mean(cmeans)) + 1e-8))
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))
    base = (tightness + stationarity + headroom) / 3.0
    return max(0.0, base - 0.05 * level)

def composite_NEW(series, reliability):
    """New composite: tightness + stationarity + headroom + reliability.
    No depth penalty — reliability replaces it."""
    vals = series.dropna().values
    if len(vals) < 15: return np.nan
    c, _ = np.histogram(vals, bins=min(25, len(vals) // 3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) /
                          (abs(np.mean(cmeans)) + 1e-8))
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))
    base = (tightness + stationarity + headroom + reliability) / 4.0
    return base

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 70)
    print("  FRAMEWORK PROPOSAL — Split-Half Reliability")
    print("  Old: (tight + station + headroom)/3 - depth_penalty")
    print("  New: (tight + station + headroom + reliability)/4, no depth pen")
    print("=" * 70)

    # [1] Data
    print("\n[1/6] Generating data...", flush=True)
    t0 = time.time()
    healthy_train = [simulate() for _ in range(N_HEALTHY)]
    healthy_held  = [simulate() for _ in range(N_HELD_OUT)]
    fault_trials = {}
    for fname in FAULT_RANGES:
        fault_trials[fname] = {}
        for sev in SEVERITIES:
            fr = FAULT_RANGES[fname]
            val = fr['healthy'] + sev * (fr['full'] - fr['healthy'])
            fault_trials[fname][sev] = [simulate(**{fr['param']: val})
                                        for _ in range(N_FAULT)]
    print(f"  Done in {time.time()-t0:.1f}s", flush=True)

    # [2] Extract features on full trials
    print("\n[2/6] Extracting features...", flush=True)
    h_train_df = pd.DataFrame([extract(r) for r in healthy_train])
    h_held_df  = pd.DataFrame([extract(r) for r in healthy_held])
    fault_dfs = {}
    for fname in FAULT_RANGES:
        fault_dfs[fname] = {}
        for sev in SEVERITIES:
            fault_dfs[fname][sev] = pd.DataFrame(
                [extract(r) for r in fault_trials[fname][sev]])

    ALL_FEATS = list(h_train_df.columns)
    print(f"  {len(ALL_FEATS)} features", flush=True)

    # Variance pre-filter
    feat_stds = h_train_df.std(axis=0)
    var_thresh = np.nanpercentile(feat_stds.dropna(), 8)
    passed = feat_stds[feat_stds >= var_thresh].index.tolist()
    print(f"  {len(passed)} pass variance filter", flush=True)

    # [3] Split-half reliability
    print("\n[3/6] Computing split-half reliability...", flush=True)
    first_halves  = [split_trial(r)[0] for r in healthy_train]
    second_halves = [split_trial(r)[1] for r in healthy_train]

    h_first_df  = pd.DataFrame([extract(r) for r in first_halves])
    h_second_df = pd.DataFrame([extract(r) for r in second_halves])

    reliability = {}
    for feat in passed:
        v1 = h_first_df[feat].values
        v2 = h_second_df[feat].values
        mask = np.isfinite(v1) & np.isfinite(v2)
        if mask.sum() < 10:
            reliability[feat] = 0.0
            continue
        r = np.corrcoef(v1[mask], v2[mask])[0, 1]
        reliability[feat] = max(0.0, r)  # clamp negative to 0

    rel_s = pd.Series(reliability).sort_values(ascending=False)
    print(f"  Reliability range: {rel_s.min():.3f} to {rel_s.max():.3f}")
    print(f"  Median reliability: {rel_s.median():.3f}")

    # [4] Score with OLD and NEW composites
    print("\n[4/6] Scoring features (old vs new composite)...", flush=True)
    scores_old = {}
    scores_new = {}
    for feat in passed:
        scores_old[feat] = composite_OLD(h_train_df[feat], feat_level(feat))
        scores_new[feat] = composite_NEW(h_train_df[feat], reliability[feat])

    old_s = pd.Series(scores_old).dropna().sort_values(ascending=False)
    new_s = pd.Series(scores_new).dropna().sort_values(ascending=False)

    # [5] AUC evaluation
    print("\n[5/6] AUC evaluation...", flush=True)
    h_mu  = h_train_df[passed].mean()
    h_sig = h_train_df[passed].std().clip(lower=1e-8)
    h_held_z = h_held_df[passed].apply(
        lambda col: (col - h_mu[col.name]).abs() / h_sig[col.name])

    auc_records = []
    for fname in FAULT_RANGES:
        for sev in SEVERITIES:
            fdf = fault_dfs[fname][sev][passed]
            f_z = fdf.apply(lambda col: (col - h_mu[col.name]).abs() / h_sig[col.name])
            for feat in passed:
                h_scores = h_held_z[feat].dropna().values
                f_scores = f_z[feat].dropna().values
                if len(h_scores) < 5 or len(f_scores) < 5:
                    auc = np.nan
                else:
                    labels = np.concatenate([np.zeros(len(h_scores)),
                                             np.ones(len(f_scores))])
                    scores = np.concatenate([h_scores, f_scores])
                    auc = roc_auc_score(labels, scores) if np.std(scores) > 1e-10 else 0.5
                auc_records.append(dict(fault=fname, severity=sev,
                                        feature=feat, AUC=auc))
    auc_df = pd.DataFrame(auc_records)
    auc_025 = auc_df[auc_df.severity == 0.25].groupby('feature')['AUC'].mean()
    auc_100 = auc_df[auc_df.severity == 1.0].groupby('feature')['AUC'].mean()

    # [6] Comparison
    print("\n[6/6] RESULTS", flush=True)
    print("=" * 70)

    # --- A. Side-by-side ranking ---
    print("\n  A. RANKING COMPARISON (top 15):")
    print(f"  {'OldRk':>5} {'NewRk':>5} {'Feature':<25} {'Old':>5} {'New':>5} "
          f"{'Rel':>5} {'AUC25':>5} {'Src':>4}")
    print("  " + "-" * 80)

    # Build combined ranking table
    all_ranked = set(old_s.index) | set(new_s.index)
    rows = []
    for feat in all_ranked:
        old_rank = list(old_s.index).index(feat) + 1 if feat in old_s.index else 99
        new_rank = list(new_s.index).index(feat) + 1 if feat in new_s.index else 99
        r = {
            'feat': feat,
            'old_rank': old_rank,
            'new_rank': new_rank,
            'old_score': old_s.get(feat, np.nan),
            'new_score': new_s.get(feat, np.nan),
            'reliability': reliability.get(feat, 0),
            'auc_025': auc_025.get(feat, np.nan),
            'auc_100': auc_100.get(feat, np.nan),
            'src': '** GP' if '_GP_' in feat else 'HC',
        }
        rows.append(r)

    # Sort by new rank
    rows.sort(key=lambda r: r['new_rank'])
    for r in rows[:15]:
        delta = r['old_rank'] - r['new_rank']
        arrow = f"+{delta}" if delta > 0 else str(delta) if delta < 0 else "="
        print(f"  {r['old_rank']:5d} {r['new_rank']:5d} {r['feat']:<25} "
              f"{r['old_score']:.3f} {r['new_score']:.3f} "
              f"{r['reliability']:.3f} {r['auc_025']:.3f} {r['src']:>4}  {arrow}")

    # --- B. GP features specifically ---
    print("\n  B. GP FEATURE MOVEMENT:")
    for r in rows:
        if '_GP_' in r['feat']:
            delta = r['old_rank'] - r['new_rank']
            print(f"     {r['feat']}: old_rank=#{r['old_rank']} -> "
                  f"new_rank=#{r['new_rank']}  (moved {'+' if delta>0 else ''}{delta})")
            print(f"       reliability={r['reliability']:.3f}  "
                  f"AUC@0.25={r['auc_025']:.3f}  AUC@1.0={r['auc_100']:.3f}")

    # --- C. Spearman: old vs new ---
    print("\n  C. SPEARMAN (old composite vs AUC, new composite vs AUC):")
    for label, comp_series in [("OLD", old_s), ("NEW", new_s)]:
        shared = comp_series.index.intersection(auc_100.index)
        cv = comp_series[shared].values
        av = auc_100[shared].values
        mask = np.isfinite(cv) & np.isfinite(av)
        if mask.sum() < 8: continue
        rho, p = stats.spearmanr(cv[mask], av[mask])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {label} composite (ALL):  rho={rho:+.3f}  p={p:.4f}  -> {tag}")

        # Per fault
        for fname in FAULT_RANGES:
            auc_fname = auc_df[(auc_df.fault == fname) & (auc_df.severity == 1.0)]
            auc_fname = auc_fname.set_index('feature')['AUC']
            common = comp_series.index.intersection(auc_fname.index)
            cv2 = comp_series[common].values
            av2 = auc_fname[common].values
            m2 = np.isfinite(cv2) & np.isfinite(av2)
            if m2.sum() < 8: continue
            rho2, p2 = stats.spearmanr(cv2[m2], av2[m2])
            tag2 = ("SUPPORTS H3" if rho2 > 0.4 and p2 < 0.05 else
                    "PARTIAL" if rho2 > 0.25 and p2 < 0.10 else "no support")
            print(f"       {fname:<15}  rho={rho2:+.3f}  p={p2:.4f}  -> {tag2}")

    # --- D. Mann-Whitney: old vs new ---
    print("\n  D. MANN-WHITNEY (top-half vs bottom-half AUC):")
    for label, comp_series in [("OLD", old_s), ("NEW", new_s)]:
        shared = comp_series.index.intersection(auc_100.index)
        ranked = list(comp_series[shared].index)
        half = len(ranked) // 2
        top_auc = np.array([auc_100.get(f, np.nan) for f in ranked[:half]])
        bot_auc = np.array([auc_100.get(f, np.nan) for f in ranked[half:]])
        tv = top_auc[np.isfinite(top_auc)]
        bv = bot_auc[np.isfinite(bot_auc)]
        if len(tv) >= 3 and len(bv) >= 3:
            U, p_mw = stats.mannwhitneyu(tv, bv, alternative='greater')
            print(f"     {label} composite:  U={U:.0f}  p={p_mw:.4f}  "
                  f"top_med={np.median(tv):.3f}  bot_med={np.median(bv):.3f}  "
                  f"-> {'SUPPORTS' if p_mw < 0.05 else 'PARTIAL' if p_mw < 0.10 else 'no support'}")

    # --- E. Precision@K and Recall@K ---
    print("\n  E. PRECISION@K / RECALL@K (AUC > 0.70 at sev=0.25 = 'good'):")
    good_feats = set(auc_025[auc_025 > 0.70].index)
    n_good = len(good_feats)
    print(f"     {n_good} features with AUC > 0.70 at sev=0.25")

    for label, comp_series in [("OLD", old_s), ("NEW", new_s)]:
        print(f"\n     {label} composite:")
        ranked = list(comp_series.index)
        for K in [5, 10, 15, 20]:
            if K > len(ranked): break
            top_K = set(ranked[:K])
            hits = top_K & good_feats
            precision = len(hits) / K
            recall = len(hits) / n_good if n_good > 0 else 0
            print(f"       @K={K:2d}:  precision={precision:.2f}  "
                  f"recall={recall:.2f}  "
                  f"hits={sorted(hits)[:3]}{'...' if len(hits)>3 else ''}")

    # --- F. Reliability breakdown ---
    print("\n  F. RELIABILITY SCORES (all features):")
    print(f"  {'Feature':<25} {'Reliability':>10} {'AUC@0.25':>8} {'Source':>6}")
    print("  " + "-" * 55)
    for feat in rel_s.index:
        src = "** GP" if '_GP_' in feat else "HC"
        a = auc_025.get(feat, np.nan)
        print(f"  {feat:<25} {rel_s[feat]:10.4f} {a:8.4f} {src:>6}")

    # --- G. Summary ---
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for r in rows:
        if '_GP_' in r['feat']:
            print(f"  {r['feat']}: #{r['old_rank']} -> #{r['new_rank']}")
    print()

    # Spearman comparison
    shared_old = old_s.index.intersection(auc_100.index)
    shared_new = new_s.index.intersection(auc_100.index)
    cv_o = old_s[shared_old].values; av_o = auc_100[shared_old].values
    cv_n = new_s[shared_new].values; av_n = auc_100[shared_new].values
    mo = np.isfinite(cv_o) & np.isfinite(av_o)
    mn = np.isfinite(cv_n) & np.isfinite(av_n)
    rho_o, _ = stats.spearmanr(cv_o[mo], av_o[mo])
    rho_n, _ = stats.spearmanr(cv_n[mn], av_n[mn])
    print(f"  Spearman (ALL, sev=1.0):  OLD rho={rho_o:+.3f}  ->  NEW rho={rho_n:+.3f}")
    print(f"  Reliability is fully unsupervised — no fault data needed.")
    print(f"  Framework: GP discovers -> composite+reliability scores -> threshold filters")
    print("=" * 70)
    print("  Done.")
