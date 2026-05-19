"""
Discovery Track — INJECTION EXPERIMENT
=======================================
Take the 26 hand-crafted features from the v4 pipeline.
Inject 2 GP-discovered features:
  GP1: ddt(omega^2)       — instantaneous power (d/dt of kinetic energy)
  GP2: rstd(theta) * omega — amplitude-modulated velocity (novel)

Run the full pipeline: composite scoring -> AUC -> Spearman + Mann-Whitney.
Question: do the GP features rank well by the SAME composite that scored
the hand-crafted features?
"""
import numpy as np
import pandas as pd
from scipy.integrate import odeint
from scipy import stats
from scipy.signal import find_peaks
from sklearn.metrics import roc_auc_score
import warnings, time
warnings.filterwarnings('ignore')

np.random.seed(7)  # same seed as original pipeline for fair comparison

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

# ── Helpers (same as original pipeline) ──────────────────────────────────────
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

# ── Feature Extraction (26 original + 2 GP) ─────────────────────────────────
def extract(run):
    t, th, om = run['t'], run['theta'], run['omega']
    g, L, fs  = run['g'], run['L'], run['fs']
    eps = 1e-8
    W20 = int(0.20 * fs); W50 = int(0.50 * fs); W100 = int(1.0 * fs)

    f = {}

    # ── L0: raw scalars ──────────────────────────────────────────────────
    f['L0_A_std']   = np.std(th)
    f['L0_A_ent']   = safe_ent(th)
    f['L0_A_kurt']  = safe_kurt(th)
    f['L0_A_skew']  = safe_skew(th)
    f['L0_A_cross'] = crossings(th)
    f['L0_V_std']   = np.std(om)
    f['L0_V_ent']   = safe_ent(om)
    f['L0_V_kurt']  = safe_kurt(om)
    f['L0_V_skew']  = safe_skew(om)

    # ── L1: one typed operation ──────────────────────────────────────────
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

    f['L1_A_rstd20']   = rolling_scalar(th, W20)
    f['L1_V_rstd20']   = rolling_scalar(om, W20)
    f['L1_E_rstd50']   = rolling_scalar(E, W50)
    f['L1_decay']       = fit_decay(t, th)
    f['L1_phase_area']  = phase_area(th, om)
    f['L1_A_range']     = np.percentile(th, 95) - np.percentile(th, 5)
    f['L1_V_range']     = np.percentile(om, 95) - np.percentile(om, 5)

    # ── L2: two typed operations ─────────────────────────────────────────
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

    # ── GP-DISCOVERED FEATURES (injected) ────────────────────────────────
    # GP1: ddt(omega^2) — instantaneous mechanical power
    #   = d/dt(omega^2) = 2*omega*alpha
    #   Contains -2b*omega^2 (damping power) explicitly
    #   Summary: std of the signal across the trial
    gp1_signal = np.gradient(om**2, 1 / fs)
    f['L2_GP_power']  = np.std(gp1_signal)

    # GP2: rstd(theta) * omega — amplitude-modulated velocity
    #   rstd(theta) ~ A(t)/sqrt(2), so product ~ A(t)*omega(t)/sqrt(2)
    #   Decays as e^{-bt} — DOUBLE the damping sensitivity
    #   Summary: std of the signal across the trial
    rstd_th = pd.Series(th).rolling(W20, min_periods=W20 // 2).std().fillna(0).values
    gp2_signal = rstd_th * om
    f['L2_GP_ampvel'] = np.std(gp2_signal)

    return f

def feat_level(name):
    return int(name[1])

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 70)
    print("  INJECTION EXPERIMENT — GP Features in Hand-Crafted Pipeline")
    print("  26 original features + 2 GP-discovered = 28 total")
    print("=" * 70)

    # [1] Generate data (same seed as original pipeline)
    print("\n[1/5] Generating data...", flush=True)
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

    # [2] Extract features
    print("\n[2/5] Extracting features (26 original + 2 GP)...", flush=True)
    h_train_df = pd.DataFrame([extract(r) for r in healthy_train])
    h_held_df  = pd.DataFrame([extract(r) for r in healthy_held])

    fault_dfs = {}
    for fname in FAULT_RANGES:
        fault_dfs[fname] = {}
        for sev in SEVERITIES:
            fault_dfs[fname][sev] = pd.DataFrame(
                [extract(r) for r in fault_trials[fname][sev]])

    ALL_FEATS = list(h_train_df.columns)
    GP_FEATS  = [f for f in ALL_FEATS if '_GP_' in f]
    HC_FEATS  = [f for f in ALL_FEATS if '_GP_' not in f]
    print(f"  {len(HC_FEATS)} hand-crafted + {len(GP_FEATS)} GP = {len(ALL_FEATS)} total",
          flush=True)

    # [3] Composite scoring
    print("\n[3/5] Composite scoring (healthy data only)...", flush=True)
    DEPTH_PENALTY = 0.05

    def composite_score(series, level):
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
        return max(0.0, base - DEPTH_PENALTY * level)

    # Variance pre-filter
    feat_stds = h_train_df.std(axis=0)
    var_thresh = np.nanpercentile(feat_stds.dropna(), 8)
    passed = feat_stds[feat_stds >= var_thresh].index.tolist()
    removed = feat_stds[feat_stds < var_thresh].index.tolist()
    print(f"  Variance filter: {len(passed)} passed, {len(removed)} removed")
    if removed:
        print(f"  Removed: {removed}")
    gp_survived = [f for f in GP_FEATS if f in passed]
    print(f"  GP features surviving filter: {gp_survived}")

    # Score
    comp = {}
    for feat in passed:
        comp[feat] = composite_score(h_train_df[feat], feat_level(feat))
    comp_s = pd.Series(comp).dropna().sort_values(ascending=False)

    print(f"\n  COMPOSITE RANKING ({len(comp_s)} features):")
    print(f"  {'Rank':>4} {'Feature':<25} {'Comp':>6} {'L':>2} {'Source':>6}")
    print("  " + "-" * 55)
    for i, (feat, val) in enumerate(comp_s.items()):
        src = "** GP" if '_GP_' in feat else "HC"
        print(f"  {i+1:4d} {feat:<25} {val:.4f} L{feat_level(feat)} {src:>6}")

    # [4] AUC evaluation
    print("\n[4/5] AUC evaluation...", flush=True)
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

    # AUC at severity=0.25 (hard case) and severity=1.0 (full fault)
    for test_sev, label in [(0.25, 'sev=0.25 (hard)'), (1.0, 'sev=1.0 (full)')]:
        auc_at = auc_df[auc_df.severity == test_sev].groupby('feature')['AUC'].mean()
        auc_at = auc_at.sort_values(ascending=False)

        print(f"\n  TOP 10 by AUC at {label}:")
        print(f"  {'Rank':>4} {'Feature':<25} {'AUC':>6} {'CompRk':>6} {'Source':>6}")
        print("  " + "-" * 55)
        for i, (feat, val) in enumerate(auc_at.head(10).items()):
            cr = list(comp_s.index).index(feat) + 1 if feat in comp_s.index else '?'
            src = "** GP" if '_GP_' in feat else "HC"
            print(f"  {i+1:4d} {feat:<25} {val:.4f} #{cr:>4} {src:>6}")

    # [5] Statistical tests
    print("\n[5/5] STATISTICAL TESTS", flush=True)
    print("=" * 70)

    # Use severity=1.0 for Spearman (same as original pipeline)
    auc_full = auc_df[auc_df.severity == 1.0].groupby('feature')['AUC'].mean()
    shared = comp_s.index.intersection(auc_full.index)

    # --- A. Spearman (all features) ---
    print("\n  A. SPEARMAN — ALL 28 features (composite vs AUC at sev=1.0):")
    spearman_results = {}
    for fname in FAULT_RANGES:
        auc_fname = auc_df[(auc_df.fault == fname) & (auc_df.severity == 1.0)]
        auc_fname = auc_fname.set_index('feature')['AUC']
        common = comp_s.index.intersection(auc_fname.index)
        cv = comp_s[common].values
        av = auc_fname[common].values
        mask = np.isfinite(cv) & np.isfinite(av)
        if mask.sum() < 8: continue
        rho, p = stats.spearmanr(cv[mask], av[mask])
        spearman_results[fname] = (rho, p)
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {fname:<15}  rho={rho:+.3f}  p={p:.4f}  n={mask.sum()}  -> {tag}")

    cv_all = comp_s[shared].values
    av_all = auc_full[shared].values
    mask = np.isfinite(cv_all) & np.isfinite(av_all)
    if mask.sum() >= 8:
        rho, p = stats.spearmanr(cv_all[mask], av_all[mask])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {'ALL':<15}  rho={rho:+.3f}  p={p:.4f}  n={mask.sum()}  -> {tag}")

    # --- B. Spearman WITHOUT GP features (original 26 only, for comparison) ---
    print("\n  B. SPEARMAN — Original 26 ONLY (baseline comparison):")
    hc_shared = [f for f in shared if '_GP_' not in f]
    for fname in FAULT_RANGES:
        auc_fname = auc_df[(auc_df.fault == fname) & (auc_df.severity == 1.0)]
        auc_fname = auc_fname.set_index('feature')['AUC']
        common = [f for f in hc_shared if f in auc_fname.index and f in comp_s.index]
        if len(common) < 8: continue
        cv = comp_s[common].values
        av = auc_fname[common].values
        mask = np.isfinite(cv) & np.isfinite(av)
        if mask.sum() < 8: continue
        rho, p = stats.spearmanr(cv[mask], av[mask])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {fname:<15}  rho={rho:+.3f}  p={p:.4f}  n={mask.sum()}  -> {tag}")

    # --- C. Mann-Whitney: top-half vs bottom-half ---
    print("\n  C. MANN-WHITNEY U — top-half vs bottom-half AUC:")
    print("     Does the composite reliably put better features in the top group?")
    n = len(shared)
    half = n // 2
    ranked_feats = list(comp_s[shared].index)
    top_feats = ranked_feats[:half]
    bot_feats = ranked_feats[half:]

    top_auc = np.array([auc_full.get(f, np.nan) for f in top_feats])
    bot_auc = np.array([auc_full.get(f, np.nan) for f in bot_feats])
    tv = top_auc[np.isfinite(top_auc)]
    bv = bot_auc[np.isfinite(bot_auc)]
    if len(tv) >= 3 and len(bv) >= 3:
        U, p_mw = stats.mannwhitneyu(tv, bv, alternative='greater')
        print(f"     ALL faults   U={U:.0f}  p={p_mw:.4f}  "
              f"top_med={np.median(tv):.3f}  bot_med={np.median(bv):.3f}  "
              f"-> {'SUPPORTS H3' if p_mw < 0.05 else 'PARTIAL' if p_mw < 0.10 else 'no support'}")

    for fname in FAULT_RANGES:
        auc_fname = auc_df[(auc_df.fault == fname) & (auc_df.severity == 1.0)]
        auc_fname = auc_fname.set_index('feature')['AUC']
        t_a = np.array([auc_fname.get(f, np.nan) for f in top_feats])
        b_a = np.array([auc_fname.get(f, np.nan) for f in bot_feats])
        tv = t_a[np.isfinite(t_a)]; bv = b_a[np.isfinite(b_a)]
        if len(tv) >= 3 and len(bv) >= 3:
            U, p_mw = stats.mannwhitneyu(tv, bv, alternative='greater')
            print(f"     {fname:<15} U={U:.0f}  p={p_mw:.4f}  "
                  f"top_med={np.median(tv):.3f}  bot_med={np.median(bv):.3f}  "
                  f"-> {'SUPPORTS H3' if p_mw < 0.05 else 'PARTIAL' if p_mw < 0.10 else 'no support'}")

    print("=" * 70)

    # --- D. GP Feature Performance Summary ---
    print("\n  D. GP FEATURE REPORT:")
    for gp_feat in GP_FEATS:
        if gp_feat not in comp_s.index:
            print(f"     {gp_feat}: REMOVED by variance filter")
            continue
        cr = list(comp_s.index).index(gp_feat) + 1
        cs = comp_s[gp_feat]
        print(f"\n     {gp_feat}:")
        print(f"       Composite: {cs:.4f}  (rank #{cr} of {len(comp_s)})")
        for fname in FAULT_RANGES:
            for sev in [0.25, 0.5, 1.0]:
                row = auc_df[(auc_df.fault == fname) & (auc_df.severity == sev)
                             & (auc_df.feature == gp_feat)]
                if len(row):
                    auc_val = row['AUC'].values[0]
                    print(f"       {fname} sev={sev}: AUC={auc_val:.4f}", end='')
                    if auc_val > 0.80:
                        print("  <<<", end='')
                    print()

    # --- E. Did injection help or hurt Spearman? ---
    print("\n  E. INJECTION IMPACT:")
    print("     Comparing Spearman WITH vs WITHOUT GP features:")
    for fname in FAULT_RANGES:
        auc_fname = auc_df[(auc_df.fault == fname) & (auc_df.severity == 1.0)]
        auc_fname = auc_fname.set_index('feature')['AUC']

        # With GP
        common_all = [f for f in shared if f in auc_fname.index]
        cv_a = comp_s[common_all].values
        av_a = auc_fname[common_all].values
        m_a = np.isfinite(cv_a) & np.isfinite(av_a)

        # Without GP
        common_hc = [f for f in hc_shared if f in auc_fname.index]
        cv_h = comp_s[common_hc].values
        av_h = auc_fname[common_hc].values
        m_h = np.isfinite(cv_h) & np.isfinite(av_h)

        if m_a.sum() >= 8 and m_h.sum() >= 8:
            rho_a, _ = stats.spearmanr(cv_a[m_a], av_a[m_a])
            rho_h, _ = stats.spearmanr(cv_h[m_h], av_h[m_h])
            delta = rho_a - rho_h
            print(f"     {fname:<15}  without_GP: rho={rho_h:+.3f}  "
                  f"with_GP: rho={rho_a:+.3f}  delta={delta:+.3f}")

    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for gp_feat in GP_FEATS:
        if gp_feat in comp_s.index:
            cr = list(comp_s.index).index(gp_feat) + 1
            auc_025 = auc_df[(auc_df.severity == 0.25) & (auc_df.feature == gp_feat)]
            auc_025_mean = auc_025['AUC'].mean() if len(auc_025) else np.nan
            auc_100 = auc_df[(auc_df.severity == 1.0) & (auc_df.feature == gp_feat)]
            auc_100_mean = auc_100['AUC'].mean() if len(auc_100) else np.nan
            print(f"  {gp_feat}: composite_rank=#{cr}/{len(comp_s)}  "
                  f"AUC@0.25={auc_025_mean:.3f}  AUC@1.0={auc_100_mean:.3f}")
    print("=" * 70)
    print("  Done.")
