"""
Discovery Track v5 — RELIABILITY-AWARE GP EVOLUTION
====================================================
The breakthrough from the framework experiment: split-half reliability
wired directly into GP fitness so evolution self-selects for physics.

New fitness = (tightness + stationarity + headroom + reliability) / 4
No depth penalty. Reliability naturally penalizes noise amplifiers.

All prior fixes preserved:
  - rstd-root penalty (0.5x)
  - Correlation-based semantic dedup (|r| > 0.95)
  - Best-ever elitism
  - Hash-based diversity penalty
  - Simplification pass

This is the full unsupervised pipeline: GP discovers features that are
simultaneously tight, stationary, headroom-friendly, AND reproducible
within each trial — from healthy data only.
"""
import numpy as np
from scipy.integrate import odeint
from scipy import stats
from sklearn.metrics import roc_auc_score
from collections import Counter
import random, sys, os, time

sys.path.insert(0, os.path.dirname(__file__))
from gp_engine import (Node, random_tree, mutate, crossover,
                        TYPES, _rstd)

np.random.seed(42)
random.seed(42)

# ── Pendulum ─────────────────────────────────────────────────────────────────
def pendulum_ode(s, t, g, L, b, Fa, Ff):
    th, om = s
    return [om, -(g/L)*np.sin(th) - b*om + Fa*np.sin(Ff*t)]

def simulate(T=25.0, fs=200.0, g=9.81, L=1.0, b=0.3,
             th0=None, Fa=0.0, Ff=2.05, noise=0.006):
    if th0 is None:
        th0 = np.pi/4 + np.random.uniform(-0.08, 0.08)
    t = np.arange(0, T, 1/fs)
    sol = odeint(pendulum_ode, [th0, 0.0], t, args=(g, L, b, Fa, Ff))
    th, om = sol[:,0].copy(), sol[:,1].copy()
    th += np.random.normal(0, noise, len(t))
    om += np.random.normal(0, noise * 0.5, len(t))
    return dict(t=t, theta=th, omega=om, g=g, L=L, fs=fs)

def safe_ent(arr, bins=25):
    v = arr[np.isfinite(arr)]
    if len(v) < 10:
        return np.nan
    c, _ = np.histogram(v, bins=bins)
    p = c / c.sum(); p = p[p > 0]
    return -np.sum(p * np.log2(p))

SUMMARIES = {
    'std':     lambda a: np.std(a),
    'entropy': lambda a: safe_ent(a),
}

# ── rstd-root detection (from v3) ───────────────────────────────────────────
def is_rstd_root(tree):
    if tree.kind == 'unary' and tree.name == 'rstd':
        return True
    if tree.kind == 'binary' and tree.name == 'mul':
        for i in range(2):
            if tree.children[i].kind == 'const' and is_rstd_root(tree.children[1 - i]):
                return True
    if tree.kind == 'unary' and tree.name in ('neg', 'abs'):
        return is_rstd_root(tree.children[0])
    return False

RSTD_PENALTY = 0.5

# ── Split trial helper ──────────────────────────────────────────────────────
def split_trial(run):
    """Cut trial at midpoint, return (first_half, second_half) as run dicts."""
    n = len(run['t'])
    mid = n // 2
    first  = {k: v[:mid] if isinstance(v, np.ndarray) else v
              for k, v in run.items()}
    second = {k: v[mid:] if isinstance(v, np.ndarray) else v
              for k, v in run.items()}
    return first, second

# ── Evaluation ───────────────────────────────────────────────────────────────
def evaluate_multi(tree, trials, fs):
    """Evaluate tree on trials, return {summary_name: array_of_scalars}."""
    results = {k: [] for k in SUMMARIES}
    for run in trials:
        signals = {'theta': run['theta'], 'omega': run['omega']}
        val = tree.evaluate(signals, fs)
        if val is None:
            return None
        finite = val[np.isfinite(val)]
        if len(finite) < 50:
            return None
        for sname, sfunc in SUMMARIES.items():
            try:
                results[sname].append(sfunc(finite))
            except:
                results[sname].append(np.nan)
    return {k: np.array(v) for k, v in results.items()}


def compute_reliability(first_vals, second_vals):
    """Pearson correlation between first-half and second-half summary values."""
    mask = np.isfinite(first_vals) & np.isfinite(second_vals)
    if mask.sum() < 10:
        return 0.0
    r = np.corrcoef(first_vals[mask], second_vals[mask])[0, 1]
    return max(0.0, r) if np.isfinite(r) else 0.0


def composite_v5(vals, reliability):
    """(tightness + stationarity + headroom + reliability) / 4. No depth penalty."""
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0
    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.005:
        return 0.0
    c, _ = np.histogram(vals, bins=min(20, len(vals) // 3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) / (abs(np.mean(cmeans)) + 1e-8))
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))
    return (tightness + stationarity + headroom + reliability) / 4.0


def fitness_v5(tree, full_trials, first_halves, second_halves, fs):
    """
    Reliability-aware fitness. Evaluates on full trials (T+S+H)
    and split-half trials (reliability). Returns (score, best_summary, reliability).
    """
    full_multi = evaluate_multi(tree, full_trials, fs)
    if full_multi is None:
        return 0.0, 'none', 0.0

    first_multi = evaluate_multi(tree, first_halves, fs)
    second_multi = evaluate_multi(tree, second_halves, fs)

    best_score, best_summary, best_rel = 0.0, 'none', 0.0
    for sname in SUMMARIES:
        full_vals = full_multi[sname]

        if first_multi is not None and second_multi is not None:
            rel = compute_reliability(first_multi[sname], second_multi[sname])
        else:
            rel = 0.0

        sc = composite_v5(full_vals, rel)
        if sc > best_score:
            best_score, best_summary, best_rel = sc, sname, rel

    if is_rstd_root(tree):
        best_score *= RSTD_PENALTY

    return best_score, best_summary, best_rel


# ── OLD composite (for comparison) ──────────────────────────────────────────
OLD_DEPTH_PENALTY = 0.02  # v4's value

def composite_old(vals, depth):
    """v4 composite: (T+S+H)/3 - depth_penalty*depth. For comparison only."""
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0
    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.005:
        return 0.0
    c, _ = np.histogram(vals, bins=min(20, len(vals) // 3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) / (abs(np.mean(cmeans)) + 1e-8))
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))
    base = (tightness + stationarity + headroom) / 3.0
    return max(0.0, base - OLD_DEPTH_PENALTY * depth)


# ── Simplify ─────────────────────────────────────────────────────────────────
def simplify(tree):
    tree = tree.copy()
    if tree.kind == 'unary' and tree.children:
        tree.children[0] = simplify(tree.children[0])
        child = tree.children[0]
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'abs':
            return child
        if tree.name == 'neg' and child.kind == 'unary' and child.name == 'neg':
            return child.children[0]
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'neg':
            tree.children[0] = child.children[0]
    if tree.kind == 'binary' and len(tree.children) == 2:
        tree.children[0] = simplify(tree.children[0])
        tree.children[1] = simplify(tree.children[1])
    return tree

# ── Diversity ────────────────────────────────────────────────────────────────
def expr_hash(tree, precision=2):
    return tree.expr_str()[:precision * 10]

def apply_diversity_penalty(scores, population):
    hashes = [expr_hash(t) for t in population]
    counts = Counter(hashes)
    return [s / counts[h] for s, h in zip(scores, hashes)]

# ── Correlation dedup ────────────────────────────────────────────────────────
def correlation_dedup(candidates, reference_trial, fs, threshold=0.95, max_keep=50):
    signals = {'theta': reference_trial['theta'], 'omega': reference_trial['omega']}
    kept = []
    kept_outputs = []

    for score, smry, rel, tree in candidates:
        if score < 0.01:
            continue
        val = tree.evaluate(signals, fs)
        if val is None or np.isfinite(val).sum() < 100:
            continue

        is_dup = False
        for ko in kept_outputs:
            n = min(len(val), len(ko))
            mask = np.isfinite(val[:n]) & np.isfinite(ko[:n])
            if mask.sum() < 100:
                continue
            try:
                r = np.corrcoef(val[:n][mask], ko[:n][mask])[0, 1]
                if abs(r) > threshold:
                    is_dup = True
                    break
            except:
                continue

        if not is_dup:
            kept.append((score, smry, rel, tree, tree.expr_str()))
            kept_outputs.append(val)
            if len(kept) >= max_keep:
                break

    return kept

# ── GP Loop ──────────────────────────────────────────────────────────────────
def run_gp(healthy_trials, first_halves, second_halves, fs,
           pop_size=200, generations=80, max_depth=4,
           tournament_k=5, elite_frac=0.05):

    print("  Initializing population...", flush=True)
    population = [simplify(random_tree(max_depth)) for _ in range(pop_size)]
    raw_scores = []; best_sums = []; reliabilities = []
    for t in population:
        sc, sm, rel = fitness_v5(t, healthy_trials, first_halves, second_halves, fs)
        raw_scores.append(sc)
        best_sums.append(sm)
        reliabilities.append(rel)
    print(f"  Init done. Best initial: {max(raw_scores):.4f}", flush=True)

    best_ever = (0.0, None, 'none', 0.0)  # (score, tree, summary, reliability)

    for gen in range(generations):
        adj_scores = apply_diversity_penalty(raw_scores, population)
        paired = sorted(zip(adj_scores, raw_scores, best_sums, reliabilities, population),
                        key=lambda x: -x[0])

        if paired[0][1] > best_ever[0]:
            best_ever = (paired[0][1], paired[0][4].copy(),
                         paired[0][2], paired[0][3])

        if gen % 10 == 0:
            uniq = len(set(t.expr_str() for _, _, _, _, t in paired[:50]))
            top = paired[0][4]
            # Average reliability of top-10
            top10_rel = np.mean([paired[i][3] for i in range(min(10, len(paired)))])
            print(f"  Gen {gen:3d}: raw={paired[0][1]:.4f} "
                  f"adj={paired[0][0]:.4f} div={uniq}/50 "
                  f"rel={paired[0][3]:.3f} top10_rel={top10_rel:.3f} "
                  f"rstd:{'Y' if is_rstd_root(top) else 'N'} "
                  f"{top.expr_str()[:35]}", flush=True)

        n_elite = max(2, int(pop_size * elite_frac))
        new_pop  = [t.copy() for _, _, _, _, t in paired[:n_elite]]
        new_raw  = [r for _, r, _, _, _ in paired[:n_elite]]
        new_sm   = [s for _, _, s, _, _ in paired[:n_elite]]
        new_rel  = [rl for _, _, _, rl, _ in paired[:n_elite]]

        # Best-ever elitism
        if best_ever[1] is not None:
            new_pop.append(best_ever[1].copy())
            new_raw.append(best_ever[0])
            new_sm.append(best_ever[2])
            new_rel.append(best_ever[3])

        while len(new_pop) < pop_size:
            def tournament():
                idxs = random.sample(range(len(paired)), min(tournament_k, len(paired)))
                return paired[max(idxs, key=lambda i: paired[i][0])][4]

            r = random.random()
            if r < 0.60:
                c1, c2 = crossover(tournament(), tournament(), max_depth)
                c1 = simplify(c1)
                sc1, sm1, rl1 = fitness_v5(c1, healthy_trials, first_halves, second_halves, fs)
                new_pop.append(c1); new_raw.append(sc1); new_sm.append(sm1); new_rel.append(rl1)
                if len(new_pop) < pop_size:
                    c2 = simplify(c2)
                    sc2, sm2, rl2 = fitness_v5(c2, healthy_trials, first_halves, second_halves, fs)
                    new_pop.append(c2); new_raw.append(sc2); new_sm.append(sm2); new_rel.append(rl2)
            elif r < 0.90:
                child = simplify(mutate(tournament(), max_depth))
                sc, sm, rl = fitness_v5(child, healthy_trials, first_halves, second_halves, fs)
                new_pop.append(child); new_raw.append(sc); new_sm.append(sm); new_rel.append(rl)
            else:
                imm = simplify(random_tree(max_depth))
                sc, sm, rl = fitness_v5(imm, healthy_trials, first_halves, second_halves, fs)
                new_pop.append(imm); new_raw.append(sc); new_sm.append(sm); new_rel.append(rl)

        population   = new_pop[:pop_size]
        raw_scores   = new_raw[:pop_size]
        best_sums    = new_sm[:pop_size]
        reliabilities = new_rel[:pop_size]

    # Ensure best_ever in final results
    paired = list(zip(raw_scores, best_sums, reliabilities, population))
    be_expr = best_ever[1].expr_str() if best_ever[1] else ''
    if not any(t.expr_str() == be_expr for _, _, _, t in paired):
        paired.append((best_ever[0], best_ever[2], best_ever[3], best_ever[1]))
    paired = sorted(paired, key=lambda x: -x[0])

    print(f"\n  BEST EVER: {best_ever[0]:.4f} rel={best_ever[3]:.3f} "
          f"via:{best_ever[2]} -- {best_ever[1].expr_str()[:70]}", flush=True)
    return paired

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    FAULTS = {
        'high_damp': {'param': 'b',  'healthy': 0.30, 'full': 0.90},
        'short_L':   {'param': 'L',  'healthy': 1.00, 'full': 0.55},
        'forcing':   {'param': 'Fa', 'healthy': 0.00, 'full': 1.30},
    }

    print("=" * 70)
    print("  DISCOVERY TRACK v5 -- Reliability-Aware GP")
    print("  fitness = (tight + station + headroom + reliability) / 4")
    print("  NO depth penalty | split-half reliability | all v3/v4 fixes")
    print("=" * 70)

    # ── [1/7] Data + pre-split ───────────────────────────────────────────────
    print("\n[1/7] Generating data + pre-splitting trials...", flush=True)
    t0 = time.time()
    healthy_train = [simulate() for _ in range(60)]
    healthy_held  = [simulate() for _ in range(20)]
    FS = healthy_train[0]['fs']

    # Pre-split for reliability (computed once, reused every fitness eval)
    first_halves  = [split_trial(r)[0] for r in healthy_train]
    second_halves = [split_trial(r)[1] for r in healthy_train]

    fault_trials = {}
    for fn, fr in FAULTS.items():
        fault_trials[fn] = {}
        for sv in [0.0, 0.25, 0.5, 0.75, 1.0]:
            v = fr['healthy'] + sv * (fr['full'] - fr['healthy'])
            fault_trials[fn][sv] = [simulate(**{fr['param']: v}) for _ in range(40)]
    print(f"  Done in {time.time()-t0:.1f}s  "
          f"(60 train + 20 held-out + 60 first-halves + 60 second-halves)", flush=True)

    # ── [2/7] GP Evolution ───────────────────────────────────────────────────
    print("\n[2/7] GP Evolution (reliability-aware fitness)...", flush=True)
    t0 = time.time()
    results = run_gp(healthy_train, first_halves, second_halves, FS)
    elapsed = time.time() - t0
    print(f"  Evolution took {elapsed:.1f}s ({elapsed/60:.1f} min)", flush=True)

    # ── [3/7] Correlation dedup ──────────────────────────────────────────────
    print("\n[3/7] Correlation dedup (|r| > 0.95)...", flush=True)
    unique = correlation_dedup(results, healthy_train[0], FS,
                               threshold=0.95, max_keep=50)
    print(f"  {len(unique)} semantically unique features", flush=True)

    # ── [4/7] Re-score with OLD composite (comparison) ───────────────────────
    print("\n[4/7] Re-scoring with OLD composite for comparison...", flush=True)
    for i, (score, smry, rel, tree, expr) in enumerate(unique):
        pass  # scores already from v5; we compute old below

    # ── [5/7] AUC evaluation ─────────────────────────────────────────────────
    print("\n[5/7] AUC evaluation...", flush=True)

    def get_auc(tree, smry, h_held, f_list, fs):
        h_m = evaluate_multi(tree, h_held, fs)
        f_m = evaluate_multi(tree, f_list, fs)
        if h_m is None or f_m is None:
            return np.nan
        s = smry if smry in h_m else 'std'
        hv = h_m[s]; fv = f_m[s]
        hv = hv[np.isfinite(hv)]; fv = fv[np.isfinite(fv)]
        if len(hv) < 5 or len(fv) < 5:
            return np.nan
        all_s = np.concatenate([hv, fv])
        if np.std(all_s) < 1e-10:
            return 0.5
        labels = np.concatenate([np.zeros(len(hv)), np.ones(len(fv))])
        try:
            a = roc_auc_score(labels, all_s)
            return max(a, 1 - a)
        except:
            return np.nan

    rows = []
    for i, (comp, smry, rel, tree, expr) in enumerate(unique):
        # v5 composite is already the score
        # compute OLD composite for comparison
        full_multi = evaluate_multi(tree, healthy_train, FS)
        if full_multi is not None:
            best_old = 0.0
            for sn in SUMMARIES:
                sc_old = composite_old(full_multi[sn], tree.depth())
                if sc_old > best_old:
                    best_old = sc_old
            if is_rstd_root(tree):
                best_old *= RSTD_PENALTY
        else:
            best_old = 0.0

        r = {'rank': i + 1, 'comp_v5': comp, 'comp_old': best_old,
             'smry': smry, 'expr': expr, 'reliability': rel,
             'depth': tree.depth(), 'type': tree.out_type,
             'rstd_root': is_rstd_root(tree)}
        for fn in FAULTS:
            r[f'auc_{fn}'] = get_auc(tree, smry, healthy_held,
                                     fault_trials[fn][0.25], FS)
        r['auc_mean'] = np.nanmean([r[f'auc_{fn}'] for fn in FAULTS])

        # Also compute AUC at full severity
        for fn in FAULTS:
            r[f'auc100_{fn}'] = get_auc(tree, smry, healthy_held,
                                        fault_trials[fn][1.0], FS)
        r['auc100_mean'] = np.nanmean([r[f'auc100_{fn}'] for fn in FAULTS])
        rows.append(r)

    # ── [6/7] Statistical Tests ──────────────────────────────────────────────
    n = len(rows)
    q = max(3, n // 4)

    print(f"\n[6/7] STATISTICAL TESTS (n={n} features)", flush=True)
    print("=" * 70)

    # -- A. Spearman: v5 composite vs AUC --
    print("\n  A. SPEARMAN — v5 composite vs AUC (sev=1.0):")
    cv5 = np.array([r['comp_v5'] for r in rows])
    auc_all = np.array([r['auc100_mean'] for r in rows])

    for fn in FAULTS:
        av = np.array([r[f'auc100_{fn}'] for r in rows])
        m = np.isfinite(cv5) & np.isfinite(av)
        if m.sum() < 8: continue
        rho, p = stats.spearmanr(cv5[m], av[m])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {fn:<15}  rho={rho:+.3f}  p={p:.4f}  -> {tag}")

    m = np.isfinite(cv5) & np.isfinite(auc_all)
    if m.sum() >= 8:
        rho, p = stats.spearmanr(cv5[m], auc_all[m])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {'ALL':<15}  rho={rho:+.3f}  p={p:.4f}  -> {tag}")

    # -- Comparison: OLD composite vs AUC --
    print("\n  A'. SPEARMAN — OLD composite vs AUC (sev=1.0) [comparison]:")
    cv_old = np.array([r['comp_old'] for r in rows])
    for fn in FAULTS:
        av = np.array([r[f'auc100_{fn}'] for r in rows])
        m = np.isfinite(cv_old) & np.isfinite(av)
        if m.sum() < 8: continue
        rho, p = stats.spearmanr(cv_old[m], av[m])
        tag = ("SUPPORTS H3" if rho > 0.4 and p < 0.05 else
               "PARTIAL" if rho > 0.25 and p < 0.10 else "no support")
        print(f"     {fn:<15}  rho={rho:+.3f}  p={p:.4f}  -> {tag}")
    m = np.isfinite(cv_old) & np.isfinite(auc_all)
    if m.sum() >= 8:
        rho_old, p_old = stats.spearmanr(cv_old[m], auc_all[m])
        tag = ("SUPPORTS H3" if rho_old > 0.4 and p_old < 0.05 else
               "PARTIAL" if rho_old > 0.25 and p_old < 0.10 else "no support")
        print(f"     {'ALL':<15}  rho={rho_old:+.3f}  p={p_old:.4f}  -> {tag}")

    # -- B. Mann-Whitney --
    print("\n  B. MANN-WHITNEY U — top-quartile vs bottom-quartile:")
    for label, comp_key in [("v5", 'comp_v5'), ("OLD", 'comp_old')]:
        ranked = sorted(rows, key=lambda r: -r[comp_key])
        top_q = np.array([r['auc100_mean'] for r in ranked[:q]])
        bot_q = np.array([r['auc100_mean'] for r in ranked[-q:]])
        tv = top_q[np.isfinite(top_q)]
        bv = bot_q[np.isfinite(bot_q)]
        if len(tv) >= 3 and len(bv) >= 3:
            U, p_mw = stats.mannwhitneyu(tv, bv, alternative='greater')
            tag = ("SUPPORTS" if p_mw < 0.05 else "PARTIAL" if p_mw < 0.10 else "no support")
            print(f"     {label:<5} ALL:  U={U:.0f}  p={p_mw:.4f}  "
                  f"top_med={np.median(tv):.3f}  bot_med={np.median(bv):.3f}  -> {tag}")

        # Per fault
        for fn in FAULTS:
            top_f = np.array([r[f'auc100_{fn}'] for r in ranked[:q]])
            bot_f = np.array([r[f'auc100_{fn}'] for r in ranked[-q:]])
            tfv = top_f[np.isfinite(top_f)]
            bfv = bot_f[np.isfinite(bot_f)]
            if len(tfv) >= 3 and len(bfv) >= 3:
                U, p_mw = stats.mannwhitneyu(tfv, bfv, alternative='greater')
                tag = ("SUPPORTS" if p_mw < 0.05 else "PARTIAL" if p_mw < 0.10 else "no support")
                print(f"     {label:<5} {fn:<15} U={U:.0f}  p={p_mw:.4f}  "
                      f"top={np.median(tfv):.3f}  bot={np.median(bfv):.3f}  -> {tag}")

    # -- C. Precision@K / Recall@K --
    print("\n  C. PRECISION@K (AUC > 0.70 at sev=0.25 = 'good'):")
    good_feats = set(r['expr'] for r in rows if r['auc_mean'] > 0.70)
    n_good = len(good_feats)
    print(f"     {n_good} features with mean AUC > 0.70 at sev=0.25")
    for label, comp_key in [("v5", 'comp_v5'), ("OLD", 'comp_old')]:
        ranked = sorted(rows, key=lambda r: -r[comp_key])
        print(f"\n     {label} composite:")
        for K in [5, 10, 15, 20]:
            if K > len(ranked): break
            top_K = set(r['expr'] for r in ranked[:K])
            hits = top_K & good_feats
            precision = len(hits) / K
            recall = len(hits) / n_good if n_good > 0 else 0
            print(f"       @K={K:2d}:  precision={precision:.2f}  recall={recall:.2f}")

    # -- D. Reliability distribution --
    print("\n  D. RELIABILITY DISTRIBUTION:")
    rels = np.array([r['reliability'] for r in rows])
    print(f"     Mean={np.mean(rels):.3f}  Median={np.median(rels):.3f}  "
          f"Min={np.min(rels):.3f}  Max={np.max(rels):.3f}")
    high_rel = sum(1 for r in rels if r > 0.9)
    low_rel  = sum(1 for r in rels if r < 0.1)
    print(f"     High (>0.9): {high_rel}/{n}  Low (<0.1): {low_rel}/{n}")

    print("=" * 70)

    # ── [7/7] Report ─────────────────────────────────────────────────────────
    print(f"\n[7/7] TOP 20 DISCOVERED FEATURES:")
    print(f"  {'#':>3} {'v5':>6} {'OLD':>6} {'Rel':>5} {'AUC25':>6} {'AUC100':>6} "
          f"{'D':>2} {'Via':>7} {'R':>1}  Expression")
    print("  " + "-" * 90)
    for r in rows[:20]:
        flag = '*' if r['rstd_root'] else ' '
        print(f"  {r['rank']:3d} {r['comp_v5']:.4f} {r['comp_old']:.4f} "
              f"{r['reliability']:.3f} {r['auc_mean']:.4f} {r['auc100_mean']:.4f} "
              f"L{r['depth']} {r['smry']:>7} {flag}  {r['expr'][:40]}")
    print("  (* = rstd at root, penalized 0.5x)")

    # Per-fault breakdown (top 10)
    print(f"\n  PER-FAULT AUC @ sev=0.25 (top 10):")
    print(f"  {'#':>3} {'hdamp':>6} {'srtL':>6} {'forc':>6} {'Rel':>5}  Expression")
    print("  " + "-" * 65)
    for r in rows[:10]:
        print(f"  {r['rank']:3d} {r.get('auc_high_damp', 0):.4f} "
              f"{r.get('auc_short_L', 0):.4f} {r.get('auc_forcing', 0):.4f} "
              f"{r['reliability']:.3f}  {r['expr'][:35]}")

    # Physics rediscovery
    print("\n  PHYSICS REDISCOVERY:")
    checks = {
        'KE (omega^2)':       False,
        'PE (cos theta)':     False,
        'Ratio feature':      False,
        'Energy composite':   False,
        'Power (sin*omega)':  False,
        'dE/dt':              False,
    }
    for r in rows[:40]:
        e = r['expr']
        if 'square' in e and 'omega' in e and not checks['KE (omega^2)']:
            print(f"    >> KE:    #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                  f"rel={r['reliability']:.3f}  {e[:50]}")
            checks['KE (omega^2)'] = True
        if 'cos' in e and 'theta' in e and not checks['PE (cos theta)']:
            print(f"    >> PE:    #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                  f"rel={r['reliability']:.3f}  {e[:50]}")
            checks['PE (cos theta)'] = True
        if 'div' in e and not checks['Ratio feature']:
            print(f"    >> Ratio: #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                  f"rel={r['reliability']:.3f}  {e[:50]}")
            checks['Ratio feature'] = True
        if 'sin' in e and ('mul' in e) and ('omega' in e or 'ddt' in e) \
                and not checks['Power (sin*omega)']:
            print(f"    >> Power: #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                  f"rel={r['reliability']:.3f}  {e[:50]}")
            checks['Power (sin*omega)'] = True
        if 'ddt' in e and ('mul' in e or 'square' in e) and not checks['dE/dt']:
            print(f"    >> dE/dt: #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                  f"rel={r['reliability']:.3f}  {e[:50]}")
            checks['dE/dt'] = True
        if ('square' in e and 'cos' in e) or \
           ('add' in e and 'square' in e and ('cos' in e or 'sin' in e)):
            if not checks['Energy composite']:
                print(f"    >> E:     #{r['rank']:2d}  AUC={r['auc_mean']:.3f}  "
                      f"rel={r['reliability']:.3f}  {e[:50]}")
                checks['Energy composite'] = True

    found = sum(checks.values())
    total = len(checks)
    print(f"\n  Found: {found}/{total} physics terms")

    # ── v4 vs v5 comparison ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  v4 vs v5 COMPARISON")
    print("=" * 70)

    # Spearman
    m5 = np.isfinite(cv5) & np.isfinite(auc_all)
    mo = np.isfinite(cv_old) & np.isfinite(auc_all)
    rho5, p5 = stats.spearmanr(cv5[m5], auc_all[m5]) if m5.sum() >= 8 else (0, 1)
    rhoO, pO = stats.spearmanr(cv_old[mo], auc_all[mo]) if mo.sum() >= 8 else (0, 1)
    print(f"  Spearman ALL (sev=1.0):  OLD rho={rhoO:+.3f} p={pO:.4f}")
    print(f"                            v5  rho={rho5:+.3f} p={p5:.4f}")

    # Avg reliability of top-10 vs bottom-10
    top10_rel = np.mean([r['reliability'] for r in rows[:10]])
    bot10_rel = np.mean([r['reliability'] for r in rows[-10:]]) if len(rows) >= 20 else 0
    print(f"  Avg reliability: top-10={top10_rel:.3f}  bottom-10={bot10_rel:.3f}")

    # Top feature
    print(f"\n  #1 feature: {rows[0]['expr'][:50]}")
    print(f"    v5={rows[0]['comp_v5']:.4f}  rel={rows[0]['reliability']:.3f}  "
          f"AUC@0.25={rows[0]['auc_mean']:.3f}  AUC@1.0={rows[0]['auc100_mean']:.3f}")

    # Count high-rel features in top-20
    high_rel_top20 = sum(1 for r in rows[:20] if r['reliability'] > 0.9)
    print(f"\n  Top-20 features with reliability > 0.9: {high_rel_top20}/20")

    print("\n" + "=" * 70)
    print("  SUMMARY: v5 — Reliability-Aware GP")
    print("=" * 70)
    print("  v1: GP collapsed to rstd() monoculture")
    print("  v2: Diversity fixed but rstd gamed composite (rho=-0.25)")
    print("  v3: rstd penalty + corr dedup -> physics at top (rho~0)")
    print("  v4: Reduced depth pen + Mann-Whitney (rho=-0.16)")
    print("  v5: Reliability IN fitness -> evolution self-selects physics")
    print(f"      Spearman ALL: OLD rho={rhoO:+.3f} -> v5 rho={rho5:+.3f}")
    print(f"      Top-10 avg reliability: {top10_rel:.3f}")
    print(f"      #1: {rows[0]['expr'][:45]}")
    print("=" * 70)
    print("  Done.")
