"""
Discovery Track — MULTI-SEED ROBUSTNESS CHECK
==============================================
Runs the v5 reliability-aware GP pipeline with multiple seeds to validate
that findings are not overfit to seed=42.

Tests:
  1. Does % of useful features (AUC>0.70) stay above 50% across seeds?
  2. Does top-10 avg reliability stay above 0.90?
  3. Does the GP consistently find physics terms?
  4. Does Mann-Whitney hold for at least some faults?

Seeds: 42 (original), 7 (from hand-crafted pipeline), 123, 99, 2024
"""
import numpy as np
from scipy.integrate import odeint
from scipy import stats
from sklearn.metrics import roc_auc_score
from collections import Counter
import random, sys, os, time, warnings
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))
from gp_engine import (Node, random_tree, mutate, crossover, TYPES, _rstd)

# ── Pendulum ──────────────────────────────────────────────────��──────────────
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
    if len(v) < 10: return np.nan
    c, _ = np.histogram(v, bins=bins)
    p = c / c.sum(); p = p[p > 0]
    return -np.sum(p * np.log2(p))

SUMMARIES = {
    'std':     lambda a: np.std(a),
    'entropy': lambda a: safe_ent(a),
}

FAULTS = {
    'high_damp': {'param': 'b',  'healthy': 0.30, 'full': 0.90},
    'short_L':   {'param': 'L',  'healthy': 1.00, 'full': 0.55},
    'forcing':   {'param': 'Fa', 'healthy': 0.00, 'full': 1.30},
}

# ── rstd-root detection ────────────────────────────────────────────────────���
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

# ── Split trial ──────────────────────────────────────────────────────��──────
def split_trial(run):
    n = len(run['t'])
    mid = n // 2
    first  = {k: v[:mid] if isinstance(v, np.ndarray) else v for k, v in run.items()}
    second = {k: v[mid:] if isinstance(v, np.ndarray) else v for k, v in run.items()}
    return first, second

# ── Evaluation ───────────────────────────��───────────────────────────────────
def evaluate_multi(tree, trials, fs):
    results = {k: [] for k in SUMMARIES}
    for run in trials:
        signals = {'theta': run['theta'], 'omega': run['omega']}
        val = tree.evaluate(signals, fs)
        if val is None: return None
        finite = val[np.isfinite(val)]
        if len(finite) < 50: return None
        for sname, sfunc in SUMMARIES.items():
            try: results[sname].append(sfunc(finite))
            except: results[sname].append(np.nan)
    return {k: np.array(v) for k, v in results.items()}

def compute_reliability(first_vals, second_vals):
    mask = np.isfinite(first_vals) & np.isfinite(second_vals)
    if mask.sum() < 10: return 0.0
    r = np.corrcoef(first_vals[mask], second_vals[mask])[0, 1]
    return max(0.0, r) if np.isfinite(r) else 0.0

def composite_v5(vals, reliability):
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10: return 0.0
    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.005: return 0.0
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
    full_multi = evaluate_multi(tree, full_trials, fs)
    if full_multi is None: return 0.0, 'none', 0.0
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

# ── Simplify & Diversity ─────────────────────────────────────────────────────
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

def expr_hash(tree, precision=2):
    return tree.expr_str()[:precision * 10]

def apply_diversity_penalty(scores, population):
    hashes = [expr_hash(t) for t in population]
    counts = Counter(hashes)
    return [s / counts[h] for s, h in zip(scores, hashes)]

# ── Correlation dedup ──────────────────────────────���─────────────────────────
def correlation_dedup(candidates, reference_trial, fs, threshold=0.95, max_keep=50):
    signals = {'theta': reference_trial['theta'], 'omega': reference_trial['omega']}
    kept = []; kept_outputs = []
    for score, smry, rel, tree in candidates:
        if score < 0.01: continue
        val = tree.evaluate(signals, fs)
        if val is None or np.isfinite(val).sum() < 100: continue
        is_dup = False
        for ko in kept_outputs:
            n = min(len(val), len(ko))
            mask = np.isfinite(val[:n]) & np.isfinite(ko[:n])
            if mask.sum() < 100: continue
            try:
                r = np.corrcoef(val[:n][mask], ko[:n][mask])[0, 1]
                if abs(r) > threshold: is_dup = True; break
            except: continue
        if not is_dup:
            kept.append((score, smry, rel, tree))
            kept_outputs.append(val)
            if len(kept) >= max_keep: break
    return kept

# ── GP Loop (same as v5 but quieter) ────────────────────────────────────────
def run_gp_quiet(healthy_trials, first_halves, second_halves, fs,
                 pop_size=200, generations=80, max_depth=4,
                 tournament_k=5, elite_frac=0.05):
    population = [simplify(random_tree(max_depth)) for _ in range(pop_size)]
    raw_scores = []; best_sums = []; reliabilities = []
    for t in population:
        sc, sm, rel = fitness_v5(t, healthy_trials, first_halves, second_halves, fs)
        raw_scores.append(sc); best_sums.append(sm); reliabilities.append(rel)

    best_ever = (0.0, None, 'none', 0.0)

    for gen in range(generations):
        adj_scores = apply_diversity_penalty(raw_scores, population)
        paired = sorted(zip(adj_scores, raw_scores, best_sums, reliabilities, population),
                        key=lambda x: -x[0])
        if paired[0][1] > best_ever[0]:
            best_ever = (paired[0][1], paired[0][4].copy(), paired[0][2], paired[0][3])

        if gen % 20 == 0:
            top10_rel = np.mean([paired[i][3] for i in range(min(10, len(paired)))])
            print(f"    Gen {gen:3d}: best={paired[0][1]:.4f} "
                  f"rel={paired[0][3]:.3f} top10_rel={top10_rel:.3f}", flush=True)

        n_elite = max(2, int(pop_size * elite_frac))
        new_pop  = [t.copy() for _, _, _, _, t in paired[:n_elite]]
        new_raw  = [r for _, r, _, _, _ in paired[:n_elite]]
        new_sm   = [s for _, _, s, _, _ in paired[:n_elite]]
        new_rel  = [rl for _, _, _, rl, _ in paired[:n_elite]]

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

        population = new_pop[:pop_size]
        raw_scores = new_raw[:pop_size]
        best_sums  = new_sm[:pop_size]
        reliabilities = new_rel[:pop_size]

    paired = list(zip(raw_scores, best_sums, reliabilities, population))
    be_expr = best_ever[1].expr_str() if best_ever[1] else ''
    if not any(t.expr_str() == be_expr for _, _, _, t in paired):
        paired.append((best_ever[0], best_ever[2], best_ever[3], best_ever[1]))
    paired = sorted(paired, key=lambda x: -x[0])
    return paired

# ── Single seed pipeline ──────────────────────────��──────────────────────────
def run_single_seed(seed):
    """Run full v5 pipeline for one seed. Returns dict of metrics."""
    np.random.seed(seed)
    random.seed(seed)

    # Generate data
    healthy_train = [simulate() for _ in range(60)]
    healthy_held  = [simulate() for _ in range(20)]
    fs = healthy_train[0]['fs']
    first_halves  = [split_trial(r)[0] for r in healthy_train]
    second_halves = [split_trial(r)[1] for r in healthy_train]

    fault_trials = {}
    for fn, fr in FAULTS.items():
        fault_trials[fn] = {}
        for sv in [0.0, 0.25, 0.5, 0.75, 1.0]:
            v = fr['healthy'] + sv * (fr['full'] - fr['healthy'])
            fault_trials[fn][sv] = [simulate(**{fr['param']: v}) for _ in range(40)]

    # Evolution
    results = run_gp_quiet(healthy_train, first_halves, second_halves, fs)

    # Dedup
    unique = correlation_dedup(results, healthy_train[0], fs, threshold=0.95, max_keep=50)
    n_unique = len(unique)

    # AUC evaluation
    def get_auc(tree, smry, h_held, f_list):
        h_m = evaluate_multi(tree, h_held, fs)
        f_m = evaluate_multi(tree, f_list, fs)
        if h_m is None or f_m is None: return np.nan
        s = smry if smry in h_m else 'std'
        hv = h_m[s]; fv = f_m[s]
        hv = hv[np.isfinite(hv)]; fv = fv[np.isfinite(fv)]
        if len(hv) < 5 or len(fv) < 5: return np.nan
        all_s = np.concatenate([hv, fv])
        if np.std(all_s) < 1e-10: return 0.5
        labels = np.concatenate([np.zeros(len(hv)), np.ones(len(fv))])
        try:
            a = roc_auc_score(labels, all_s)
            return max(a, 1 - a)
        except: return np.nan

    rows = []
    for i, (comp, smry, rel, tree) in enumerate(unique):
        r = {'comp': comp, 'rel': rel, 'expr': tree.expr_str(), 'depth': tree.depth()}
        for fn in FAULTS:
            r[f'auc025_{fn}'] = get_auc(tree, smry, healthy_held, fault_trials[fn][0.25])
            r[f'auc100_{fn}'] = get_auc(tree, smry, healthy_held, fault_trials[fn][1.0])
        r['auc025_mean'] = np.nanmean([r[f'auc025_{fn}'] for fn in FAULTS])
        r['auc100_mean'] = np.nanmean([r[f'auc100_{fn}'] for fn in FAULTS])
        rows.append(r)

    # Metrics
    n = len(rows)
    rels = [r['rel'] for r in rows]
    aucs_025 = [r['auc025_mean'] for r in rows]
    aucs_100 = [r['auc100_mean'] for r in rows]
    comps = [r['comp'] for r in rows]

    pct_good = sum(1 for a in aucs_025 if a > 0.70) / n if n > 0 else 0
    pct_high_rel = sum(1 for r in rels if r > 0.9) / n if n > 0 else 0
    top10_rel = np.mean(sorted(rels, reverse=True)[:10])
    best_auc = max(aucs_025) if aucs_025 else 0

    # Spearman
    cv = np.array(comps)
    av = np.array(aucs_100)
    m = np.isfinite(cv) & np.isfinite(av)
    if m.sum() >= 8:
        rho_all, p_all = stats.spearmanr(cv[m], av[m])
    else:
        rho_all, p_all = 0, 1

    # Per-fault Spearman
    spearman_faults = {}
    for fn in FAULTS:
        afv = np.array([r[f'auc100_{fn}'] for r in rows])
        mf = np.isfinite(cv) & np.isfinite(afv)
        if mf.sum() >= 8:
            rho_f, p_f = stats.spearmanr(cv[mf], afv[mf])
            spearman_faults[fn] = (rho_f, p_f)
        else:
            spearman_faults[fn] = (0, 1)

    # Mann-Whitney
    q = max(3, n // 4)
    ranked = sorted(rows, key=lambda r: -r['comp'])
    mw_results = {}
    for fn in FAULTS:
        top_f = np.array([r[f'auc100_{fn}'] for r in ranked[:q]])
        bot_f = np.array([r[f'auc100_{fn}'] for r in ranked[-q:]])
        tv = top_f[np.isfinite(top_f)]
        bv = bot_f[np.isfinite(bot_f)]
        if len(tv) >= 3 and len(bv) >= 3:
            U, p_mw = stats.mannwhitneyu(tv, bv, alternative='greater')
            mw_results[fn] = (U, p_mw)
        else:
            mw_results[fn] = (0, 1)

    # Physics check
    physics_found = 0
    checks = {'KE': False, 'PE': False, 'Ratio': False, 'Energy': False,
              'Power': False, 'dE/dt': False}
    for r in rows[:40]:
        e = r['expr']
        if 'square' in e and 'omega' in e and not checks['KE']:
            checks['KE'] = True
        if 'cos' in e and 'theta' in e and not checks['PE']:
            checks['PE'] = True
        if 'div' in e and not checks['Ratio']:
            checks['Ratio'] = True
        if 'sin' in e and 'mul' in e and ('omega' in e or 'ddt' in e) and not checks['Power']:
            checks['Power'] = True
        if 'ddt' in e and ('mul' in e or 'square' in e) and not checks['dE/dt']:
            checks['dE/dt'] = True
        if ('square' in e and 'cos' in e) or \
           ('add' in e and 'square' in e and ('cos' in e or 'sin' in e)):
            if not checks['Energy']:
                checks['Energy'] = True
    physics_found = sum(checks.values())

    return {
        'seed': seed,
        'n_unique': n_unique,
        'pct_good_025': pct_good,
        'pct_high_rel': pct_high_rel,
        'top10_rel': top10_rel,
        'best_auc_025': best_auc,
        'best_comp': max(comps) if comps else 0,
        'rho_all': rho_all,
        'p_all': p_all,
        'spearman_faults': spearman_faults,
        'mw_results': mw_results,
        'physics_found': physics_found,
        'physics_checks': checks,
        'top1_expr': rows[0]['expr'][:60] if rows else 'N/A',
        'top1_auc': rows[0]['auc025_mean'] if rows else 0,
        'top1_rel': rows[0]['rel'] if rows else 0,
    }

# ── MAIN ───────────────────────���─────────────────────────────────────────────
if __name__ == '__main__':
    SEEDS = [42, 7, 123, 99, 2024]

    print("=" * 70)
    print("  MULTI-SEED ROBUSTNESS CHECK - v5 Pipeline")
    print(f"  Seeds: {SEEDS}")
    print(f"  Each run: 200 pop x 80 gen, reliability-aware fitness")
    print("=" * 70)

    all_results = []
    total_t0 = time.time()

    for i, seed in enumerate(SEEDS):
        print(f"\n{'-'*70}")
        print(f"  SEED {seed} ({i+1}/{len(SEEDS)})", flush=True)
        print(f"{'-'*70}")
        t0 = time.time()
        result = run_single_seed(seed)
        elapsed = time.time() - t0
        result['elapsed_s'] = elapsed
        all_results.append(result)

        print(f"\n  Seed {seed} results ({elapsed/60:.1f} min):")
        print(f"    Unique features: {result['n_unique']}")
        print(f"    % good (AUC>0.70): {result['pct_good_025']*100:.0f}%")
        print(f"    % high reliability: {result['pct_high_rel']*100:.0f}%")
        print(f"    Top-10 avg rel: {result['top10_rel']:.3f}")
        print(f"    Best AUC@0.25: {result['best_auc_025']:.3f}")
        print(f"    Physics found: {result['physics_found']}/6")
        print(f"    Spearman ALL: rho={result['rho_all']:+.3f} p={result['p_all']:.4f}")
        print(f"    Top feature: {result['top1_expr']}")
        for fn in FAULTS:
            rho_f, p_f = result['spearman_faults'][fn]
            U_f, p_mw = result['mw_results'][fn]
            tag_s = "H3" if rho_f > 0.3 and p_f < 0.05 else "partial" if rho_f > 0.2 else "-"
            tag_m = "H3" if p_mw < 0.05 else "partial" if p_mw < 0.10 else "-"
            print(f"      {fn:<12} Spear: rho={rho_f:+.3f} [{tag_s}]  "
                  f"MW: p={p_mw:.3f} [{tag_m}]")

    # ── AGGREGATE ────────────────────────────────────────────────────────────
    total_elapsed = time.time() - total_t0
    print(f"\n\n{'='*70}")
    print(f"  AGGREGATE RESULTS ({len(SEEDS)} seeds, {total_elapsed/60:.1f} min total)")
    print(f"{'='*70}")

    pct_goods = [r['pct_good_025'] for r in all_results]
    pct_rels  = [r['pct_high_rel'] for r in all_results]
    top10_rels = [r['top10_rel'] for r in all_results]
    physics_counts = [r['physics_found'] for r in all_results]
    rhos = [r['rho_all'] for r in all_results]
    best_aucs = [r['best_auc_025'] for r in all_results]

    print(f"\n  {'Metric':<30} {'Mean':>8} {'Min':>8} {'Max':>8} {'Std':>8}")
    print(f"  {'-'*66}")
    print(f"  {'% good features (AUC>0.70)':<30} {np.mean(pct_goods)*100:7.1f}% "
          f"{np.min(pct_goods)*100:7.1f}% {np.max(pct_goods)*100:7.1f}% "
          f"{np.std(pct_goods)*100:7.1f}%")
    print(f"  {'% high reliability (>0.9)':<30} {np.mean(pct_rels)*100:7.1f}% "
          f"{np.min(pct_rels)*100:7.1f}% {np.max(pct_rels)*100:7.1f}% "
          f"{np.std(pct_rels)*100:7.1f}%")
    print(f"  {'Top-10 avg reliability':<30} {np.mean(top10_rels):8.3f} "
          f"{np.min(top10_rels):8.3f} {np.max(top10_rels):8.3f} "
          f"{np.std(top10_rels):8.3f}")
    print(f"  {'Best AUC@0.25':<30} {np.mean(best_aucs):8.3f} "
          f"{np.min(best_aucs):8.3f} {np.max(best_aucs):8.3f} "
          f"{np.std(best_aucs):8.3f}")
    print(f"  {'Physics terms (/6)':<30} {np.mean(physics_counts):8.1f} "
          f"{np.min(physics_counts):8d} {np.max(physics_counts):8d} "
          f"{np.std(physics_counts):8.1f}")
    print(f"  {'Spearman rho (ALL)':<30} {np.mean(rhos):+7.3f} "
          f"{np.min(rhos):+7.3f} {np.max(rhos):+7.3f} "
          f"{np.std(rhos):8.3f}")

    # Per-fault aggregates
    print(f"\n  PER-FAULT MANN-WHITNEY (% of seeds with p < 0.05):")
    for fn in FAULTS:
        p_vals = [r['mw_results'][fn][1] for r in all_results]
        n_sig = sum(1 for p in p_vals if p < 0.05)
        n_part = sum(1 for p in p_vals if p < 0.10)
        print(f"    {fn:<15} significant: {n_sig}/{len(SEEDS)}  "
              f"partial: {n_part}/{len(SEEDS)}  "
              f"median_p={np.median(p_vals):.4f}")

    print(f"\n  PER-FAULT SPEARMAN (% of seeds with rho > 0.25):")
    for fn in FAULTS:
        rho_vals = [r['spearman_faults'][fn][0] for r in all_results]
        n_pos = sum(1 for rho in rho_vals if rho > 0.25)
        print(f"    {fn:<15} rho>0.25: {n_pos}/{len(SEEDS)}  "
              f"mean_rho={np.mean(rho_vals):+.3f}  "
              f"range=[{np.min(rho_vals):+.3f}, {np.max(rho_vals):+.3f}]")

    # Robustness verdict
    print(f"\n  {'-'*66}")
    print(f"  ROBUSTNESS VERDICT:")
    checks_pass = []

    c1 = np.mean(pct_goods) > 0.50
    checks_pass.append(c1)
    print(f"    [{'PASS' if c1 else 'FAIL'}] Mean % good features > 50%: "
          f"{np.mean(pct_goods)*100:.0f}%")

    c2 = np.mean(top10_rels) > 0.90
    checks_pass.append(c2)
    print(f"    [{'PASS' if c2 else 'FAIL'}] Mean top-10 reliability > 0.90: "
          f"{np.mean(top10_rels):.3f}")

    c3 = np.min(physics_counts) >= 4
    checks_pass.append(c3)
    print(f"    [{'PASS' if c3 else 'FAIL'}] Min physics terms >= 4/6: "
          f"{np.min(physics_counts)}/6")

    c4 = any(r['mw_results']['high_damp'][1] < 0.10 for r in all_results)
    checks_pass.append(c4)
    print(f"    [{'PASS' if c4 else 'FAIL'}] At least one seed MW supports high_damp")

    c5 = np.std(pct_goods) < 0.20
    checks_pass.append(c5)
    print(f"    [{'PASS' if c5 else 'FAIL'}] Low variance in % good (std < 20%): "
          f"std={np.std(pct_goods)*100:.1f}%")

    n_pass = sum(checks_pass)
    print(f"\n    OVERALL: {n_pass}/{len(checks_pass)} checks passed")
    if n_pass == len(checks_pass):
        print(f"    => FRAMEWORK IS ROBUST across seeds")
    elif n_pass >= 3:
        print(f"    => FRAMEWORK IS PARTIALLY ROBUST (some variability)")
    else:
        print(f"    => FRAMEWORK NEEDS INVESTIGATION")

    print(f"\n{'='*70}")
    print(f"  Done. Total time: {total_elapsed/60:.1f} min")
    print(f"{'='*70}")
