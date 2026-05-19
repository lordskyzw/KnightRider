"""
Real-World Experiment: CWRU Bearing Dataset
============================================
Apply the v5 reliability-aware GP framework to real vibration data.

Goal: Can features discovered unsupervised (normal data only) match or
approach supervised fault-detection performance?

Data: CWRU Bearing Data Center, 12kHz, 1797 RPM (0 HP load)
Signals: DE (drive-end accel), FE (fan-end accel)
Faults: IR (inner race), B (ball), OR@6 (outer race centered @6 o'clock)
Severities: 0.007", 0.014", 0.021" fault diameters

Design:
  - Window normal recording into 0.25s segments (3000 samples, ~7.5 rotations)
  - 60 windows for GP training, 20 for held-out evaluation
  - Fault windows: 40 per condition (for AUC evaluation only)
  - GP evolves features from DE + FE using reliability-aware fitness
  - Compare GP features vs standard hand-crafted bearing features
"""
import numpy as np
from scipy import stats
from sklearn.metrics import roc_auc_score
from collections import Counter
from copy import deepcopy
import random, sys, os, time, warnings
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

np.random.seed(42)
random.seed(42)

# ── Data Loading ─────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'Data', '1797 RPM')
FS = 12000  # 12kHz sampling rate
WINDOW = 3000  # 0.25s window = ~7.5 shaft rotations at 1797 RPM
N_TRAIN = 60
N_HELD = 20

def load_signal(fname, keys=('DE', 'FE')):
    """Load .npz file, return dict of flattened arrays."""
    path = os.path.join(DATA_DIR, fname)
    data = np.load(path)
    return {k: data[k].flatten() for k in keys if k in data}

def window_signal(signals, window_size, max_windows=None):
    """Segment signals into non-overlapping windows. Returns list of dicts."""
    n = min(len(v) for v in signals.values())
    n_windows = n // window_size
    if max_windows:
        n_windows = min(n_windows, max_windows)
    windows = []
    for i in range(n_windows):
        start = i * window_size
        end = start + window_size
        w = {k: v[start:end] for k, v in signals.items()}
        w['fs'] = FS
        windows.append(w)
    return windows

# ── Vibration Type System ────────────────────────────────────────────────────
# Simpler than pendulum: both DE and FE are vibration (acceleration) signals
TYPES = ('V', 'E', 'S')  # Vibration, Energy, Scalar

TERMINALS = {'DE': 'V', 'FE': 'V'}
CONSTS = [0.5, 1.0, 2.0, 5.0]

# ── Operators ────────────────────────────────────────────────────────────────
OPS = []

# Derivative (vibration → vibration, like jerk)
OPS.append(('ddt', 1, ('V',), 'V', lambda a, fs: np.gradient(a, 1/fs)))
OPS.append(('ddt', 1, ('E',), 'E', lambda a, fs: np.gradient(a, 1/fs)))

# Shape operators
OPS.append(('abs', 1, ('V',), 'V', lambda a, fs: np.abs(a)))
OPS.append(('abs', 1, ('E',), 'E', lambda a, fs: np.abs(a)))
OPS.append(('neg', 1, ('V',), 'V', lambda a, fs: -a))
OPS.append(('neg', 1, ('S',), 'S', lambda a, fs: -a))
OPS.append(('square', 1, ('V',), 'E', lambda a, fs: a**2))  # vibration^2 = energy
OPS.append(('square', 1, ('S',), 'S', lambda a, fs: a**2))

# Rolling std (temporal aggregation — key for vibration analysis)
def _rstd(a, fs, w=60):  # 60 samples = 5ms at 12kHz
    return pd.Series(a).rolling(w, min_periods=w//2).std().fillna(0).values

OPS.append(('rstd', 1, ('V',), 'V', lambda a, fs: _rstd(a, fs)))
OPS.append(('rstd', 1, ('E',), 'E', lambda a, fs: _rstd(a, fs)))

# Envelope (abs of signal — crude envelope extraction)
OPS.append(('env', 1, ('V',), 'V', lambda a, fs: np.abs(a)))

# Binary same-type (add, sub)
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Binary products
OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))  # DE*FE → energy
OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))

# Binary ratios
OPS.append(('div', 2, ('V', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))

# Build lookup tables
UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1]==1 and o[2]==(t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1]==2 and o[2]==key]

# ── Expression Tree ──────────────────────────────────────────────────────────
class Node:
    __slots__ = ('kind', 'name', 'out_type', 'func', 'children', 'const_val')
    def __init__(self, kind, name, out_type, func=None, children=None, const_val=None):
        self.kind = kind
        self.name = name
        self.out_type = out_type
        self.func = func
        self.children = children or []
        self.const_val = const_val

    def depth(self):
        if not self.children: return 0
        return 1 + max(c.depth() for c in self.children)

    def size(self):
        return 1 + sum(c.size() for c in self.children)

    def evaluate(self, signals, fs):
        try:
            if self.kind == 'terminal':
                return signals[self.name].copy()
            elif self.kind == 'const':
                return np.full(len(signals['DE']), self.const_val)
            elif self.kind == 'unary':
                child_val = self.children[0].evaluate(signals, fs)
                return self.func(child_val, fs)
            elif self.kind == 'binary':
                left = self.children[0].evaluate(signals, fs)
                right = self.children[1].evaluate(signals, fs)
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except:
            return None

    def expr_str(self):
        if self.kind == 'terminal': return self.name
        elif self.kind == 'const': return f"{self.const_val:.2f}"
        elif self.kind == 'unary': return f"{self.name}({self.children[0].expr_str()})"
        elif self.kind == 'binary':
            return f"({self.children[0].expr_str()} {self.name} {self.children[1].expr_str()})"

    def copy(self):
        return deepcopy(self)

# ── Tree Generation ──────────────────────────────────────────────────────────
def random_terminal(target_type=None):
    options = []
    for name, typ in TERMINALS.items():
        if target_type is None or typ == target_type:
            options.append(Node('terminal', name, typ))
    if target_type is None or target_type == 'S':
        c = random.choice(CONSTS)
        options.append(Node('const', f'c{c}', 'S', const_val=c))
    if not options:
        name = random.choice(list(TERMINALS.keys()))
        return Node('terminal', name, TERMINALS[name])
    return random.choice(options)

def random_tree(max_depth, target_type=None):
    if max_depth <= 0:
        return random_terminal(target_type)
    if random.random() < 0.3:
        t = random_terminal(target_type)
        if target_type is None or t.out_type == target_type:
            return t
    if random.random() < 0.5:
        candidates = []
        for in_type in TYPES:
            for name, func, out in UNARY_OPS.get(in_type, []):
                if target_type is None or out == target_type:
                    candidates.append((name, func, out, in_type))
        if candidates:
            name, func, out, in_type = random.choice(candidates)
            child = random_tree(max_depth - 1, in_type)
            return Node('unary', name, out, func, [child])
    candidates = []
    for (t1, t2), ops in BINARY_OPS.items():
        for name, func, out in ops:
            if target_type is None or out == target_type:
                candidates.append((name, func, out, t1, t2))
    if candidates:
        name, func, out, t1, t2 = random.choice(candidates)
        left = random_tree(max_depth - 1, t1)
        right = random_tree(max_depth - 1, t2)
        return Node('binary', name, out, func, [left, right])
    return random_terminal(target_type)

# ── Tree Manipulation ────────────────────────────────────────────────────────
def _all_nodes(node, path, parent, idx):
    yield node, path, parent, idx
    for i, child in enumerate(node.children):
        yield from _all_nodes(child, path + [i], node, i)

def mutate(tree, max_depth=4):
    tree = tree.copy()
    nodes = list(_all_nodes(tree, [], None, None))
    if not nodes: return tree
    node, path, parent, idx = random.choice(nodes)
    new_sub = random_tree(max(1, max_depth - len(path)), node.out_type)
    if parent is None: return new_sub
    parent.children[idx] = new_sub
    return tree

def crossover(t1, t2, max_depth=4):
    t1, t2 = t1.copy(), t2.copy()
    nodes1 = list(_all_nodes(t1, [], None, None))
    nodes2 = list(_all_nodes(t2, [], None, None))
    random.shuffle(nodes1)
    for n1, p1, par1, idx1 in nodes1:
        compatible = [(n2, p2, par2, idx2) for n2, p2, par2, idx2 in nodes2
                      if n2.out_type == n1.out_type and len(p2) + n1.depth() <= max_depth]
        if compatible:
            n2, p2, par2, idx2 = random.choice(compatible)
            if par1 is not None and par2 is not None:
                par1.children[idx1] = n2
                par2.children[idx2] = n1
                return t1, t2
    return t1, t2

def simplify(tree):
    tree = tree.copy()
    if tree.kind == 'unary' and tree.children:
        tree.children[0] = simplify(tree.children[0])
        child = tree.children[0]
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'abs':
            return child
        if tree.name == 'neg' and child.kind == 'unary' and child.name == 'neg':
            return child.children[0]
    if tree.kind == 'binary' and len(tree.children) == 2:
        tree.children[0] = simplify(tree.children[0])
        tree.children[1] = simplify(tree.children[1])
    return tree

# ── rstd-root detection ─────────────────────────────────────────────────────
def is_rstd_root(tree):
    if tree.kind == 'unary' and tree.name == 'rstd': return True
    if tree.kind == 'binary' and tree.name == 'mul':
        for i in range(2):
            if tree.children[i].kind == 'const' and is_rstd_root(tree.children[1-i]):
                return True
    if tree.kind == 'unary' and tree.name in ('neg', 'abs'):
        return is_rstd_root(tree.children[0])
    return False

RSTD_PENALTY = 0.5

# ── Split-half & Fitness ────────────────────────────────────────────────────
def split_window(w):
    mid = WINDOW // 2
    first = {k: v[:mid] if isinstance(v, np.ndarray) else v for k, v in w.items()}
    second = {k: v[mid:] if isinstance(v, np.ndarray) else v for k, v in w.items()}
    return first, second

SUMMARIES = {
    'std': lambda a: np.std(a),
    'rms': lambda a: np.sqrt(np.mean(a**2)),
}

def evaluate_multi(tree, windows, fs):
    results = {k: [] for k in SUMMARIES}
    for w in windows:
        signals = {k: v for k, v in w.items() if isinstance(v, np.ndarray)}
        val = tree.evaluate(signals, fs)
        if val is None: return None
        finite = val[np.isfinite(val)]
        if len(finite) < 50: return None
        for sn, sf in SUMMARIES.items():
            try: results[sn].append(sf(finite))
            except: results[sn].append(np.nan)
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

def fitness_v5(tree, full_windows, first_halves, second_halves, fs):
    full_multi = evaluate_multi(tree, full_windows, fs)
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

# ── Diversity & Dedup ────────────────────────────────────────────────────────
def expr_hash(tree, precision=2):
    return tree.expr_str()[:precision * 10]

def apply_diversity_penalty(scores, population):
    hashes = [expr_hash(t) for t in population]
    counts = Counter(hashes)
    return [s / counts[h] for s, h in zip(scores, hashes)]

def correlation_dedup(candidates, ref_window, fs, threshold=0.95, max_keep=50):
    signals = {k: v for k, v in ref_window.items() if isinstance(v, np.ndarray)}
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

# ── GP Evolution ─────────────────────────────────────────────────────────────
def run_gp(train_windows, first_halves, second_halves, fs,
           pop_size=200, generations=80, max_depth=4,
           tournament_k=5, elite_frac=0.05):

    print("  Initializing population...", flush=True)
    population = [simplify(random_tree(max_depth)) for _ in range(pop_size)]
    raw_scores = []; best_sums = []; reliabilities = []
    for t in population:
        sc, sm, rel = fitness_v5(t, train_windows, first_halves, second_halves, fs)
        raw_scores.append(sc); best_sums.append(sm); reliabilities.append(rel)
    print(f"  Init done. Best: {max(raw_scores):.4f}", flush=True)

    best_ever = (0.0, None, 'none', 0.0)

    for gen in range(generations):
        adj_scores = apply_diversity_penalty(raw_scores, population)
        paired = sorted(zip(adj_scores, raw_scores, best_sums, reliabilities, population),
                        key=lambda x: -x[0])
        if paired[0][1] > best_ever[0]:
            best_ever = (paired[0][1], paired[0][4].copy(), paired[0][2], paired[0][3])

        if gen % 10 == 0:
            uniq = len(set(t.expr_str() for _, _, _, _, t in paired[:50]))
            top10_rel = np.mean([paired[i][3] for i in range(min(10, len(paired)))])
            print(f"  Gen {gen:3d}: best={paired[0][1]:.4f} "
                  f"rel={paired[0][3]:.3f} top10_rel={top10_rel:.3f} "
                  f"div={uniq}/50 "
                  f"{paired[0][4].expr_str()[:40]}", flush=True)

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
                sc1, sm1, rl1 = fitness_v5(c1, train_windows, first_halves, second_halves, fs)
                new_pop.append(c1); new_raw.append(sc1); new_sm.append(sm1); new_rel.append(rl1)
                if len(new_pop) < pop_size:
                    c2 = simplify(c2)
                    sc2, sm2, rl2 = fitness_v5(c2, train_windows, first_halves, second_halves, fs)
                    new_pop.append(c2); new_raw.append(sc2); new_sm.append(sm2); new_rel.append(rl2)
            elif r < 0.90:
                child = simplify(mutate(tournament(), max_depth))
                sc, sm, rl = fitness_v5(child, train_windows, first_halves, second_halves, fs)
                new_pop.append(child); new_raw.append(sc); new_sm.append(sm); new_rel.append(rl)
            else:
                imm = simplify(random_tree(max_depth))
                sc, sm, rl = fitness_v5(imm, train_windows, first_halves, second_halves, fs)
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

    print(f"\n  BEST EVER: {best_ever[0]:.4f} rel={best_ever[3]:.3f} "
          f"-- {best_ever[1].expr_str()[:70]}", flush=True)
    return paired

# ── Hand-Crafted Baseline Features ──────────────────────────────────────────
def extract_baseline(w):
    """Standard bearing diagnostic features for comparison."""
    de = w['DE']
    fe = w['FE']
    f = {}
    # RMS
    f['DE_rms'] = np.sqrt(np.mean(de**2))
    f['FE_rms'] = np.sqrt(np.mean(fe**2))
    # Std
    f['DE_std'] = np.std(de)
    f['FE_std'] = np.std(fe)
    # Kurtosis (classic fault indicator)
    f['DE_kurt'] = float(stats.kurtosis(de, fisher=True))
    f['FE_kurt'] = float(stats.kurtosis(fe, fisher=True))
    # Crest factor (peak / RMS)
    f['DE_crest'] = np.max(np.abs(de)) / (np.sqrt(np.mean(de**2)) + 1e-8)
    f['FE_crest'] = np.max(np.abs(fe)) / (np.sqrt(np.mean(fe**2)) + 1e-8)
    # Skewness
    f['DE_skew'] = float(stats.skew(de))
    f['FE_skew'] = float(stats.skew(fe))
    # Peak-to-peak
    f['DE_p2p'] = np.max(de) - np.min(de)
    f['FE_p2p'] = np.max(fe) - np.min(fe)
    # Energy (sum of squares)
    f['DE_energy'] = np.sum(de**2)
    f['FE_energy'] = np.sum(fe**2)
    # Cross-channel
    f['DEFE_corr'] = float(np.corrcoef(de, fe)[0, 1])
    f['DEFE_ratio'] = f['DE_rms'] / (f['FE_rms'] + 1e-8)
    return f

# ── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    FAULTS = {
        'IR_7':   '1797_IR_7_DE12.npz',    # inner race, 0.007"
        'IR_14':  '1797_IR_14_DE12.npz',   # inner race, 0.014"
        'IR_21':  '1797_IR_21_DE12.npz',   # inner race, 0.021"
        'B_7':    '1797_B_7_DE12.npz',     # ball, 0.007"
        'B_14':   '1797_B_14_DE12.npz',    # ball, 0.014"
        'B_21':   '1797_B_21_DE12.npz',    # ball, 0.021"
        'OR6_7':  '1797_OR@6_7_DE12.npz',  # outer race @6, 0.007"
        'OR6_14': '1797_OR@6_14_DE12.npz', # outer race @6, 0.014"
        'OR6_21': '1797_OR@6_21_DE12.npz', # outer race @6, 0.021"
    }

    print("=" * 70)
    print("  REAL-WORLD EXPERIMENT: CWRU Bearing Dataset")
    print("  Reliability-aware GP on vibration data")
    print("  Signals: DE + FE | 12kHz | 1797 RPM | 0.25s windows")
    print("=" * 70)

    # ── [1/6] Load & window data ─────────────────────────────────────────────
    print("\n[1/6] Loading CWRU data...", flush=True)
    t0 = time.time()

    normal_sig = load_signal('1797_Normal.npz')
    all_normal_windows = window_signal(normal_sig, WINDOW)
    np.random.shuffle(all_normal_windows)

    train_windows = all_normal_windows[:N_TRAIN]
    held_windows  = all_normal_windows[N_TRAIN:N_TRAIN + N_HELD]
    print(f"  Normal: {len(all_normal_windows)} windows total, "
          f"{len(train_windows)} train, {len(held_windows)} held-out")

    # Pre-split for reliability
    first_halves  = [split_window(w)[0] for w in train_windows]
    second_halves = [split_window(w)[1] for w in train_windows]

    # Load fault data
    fault_windows = {}
    for fname, ffile in FAULTS.items():
        fsig = load_signal(ffile)
        fw = window_signal(fsig, WINDOW, max_windows=40)
        fault_windows[fname] = fw
        print(f"  {fname}: {len(fw)} windows")

    print(f"  Done in {time.time()-t0:.1f}s", flush=True)

    # ── [2/6] Baseline features ──────────────────────────────────────────────
    print("\n[2/6] Computing baseline hand-crafted features...", flush=True)
    baseline_train = pd.DataFrame([extract_baseline(w) for w in train_windows])
    baseline_held  = pd.DataFrame([extract_baseline(w) for w in held_windows])
    baseline_faults = {}
    for fname, fws in fault_windows.items():
        baseline_faults[fname] = pd.DataFrame([extract_baseline(w) for w in fws])

    # Baseline AUC
    b_feats = list(baseline_train.columns)
    b_mu  = baseline_train.mean()
    b_sig = baseline_train.std().clip(lower=1e-8)

    print(f"  {len(b_feats)} baseline features")
    print(f"\n  BASELINE AUC (hand-crafted features):")
    print(f"  {'Feature':<15} ", end='')
    for fname in FAULTS:
        print(f"{fname:>7}", end='')
    print()
    print("  " + "-" * 85)

    baseline_aucs = {}
    for feat in b_feats:
        h_z = (baseline_held[feat] - b_mu[feat]).abs() / b_sig[feat]
        row_aucs = {}
        for fname in FAULTS:
            f_z = (baseline_faults[fname][feat] - b_mu[feat]).abs() / b_sig[feat]
            hv = h_z.dropna().values
            fv = f_z.dropna().values
            if len(hv) >= 5 and len(fv) >= 5:
                labels = np.concatenate([np.zeros(len(hv)), np.ones(len(fv))])
                scores = np.concatenate([hv, fv])
                try:
                    auc = roc_auc_score(labels, scores)
                    auc = max(auc, 1 - auc)
                except:
                    auc = 0.5
            else:
                auc = 0.5
            row_aucs[fname] = auc
        baseline_aucs[feat] = row_aucs
        mean_auc = np.mean(list(row_aucs.values()))
        print(f"  {feat:<15} ", end='')
        for fname in FAULTS:
            a = row_aucs[fname]
            print(f"{a:7.3f}", end='')
        print(f"  mean={mean_auc:.3f}")

    # ── [3/6] GP Evolution ───────────────────────────────────────────────────
    print(f"\n[3/6] GP Evolution (reliability-aware fitness)...", flush=True)
    t0 = time.time()
    results = run_gp(train_windows, first_halves, second_halves, FS)
    elapsed = time.time() - t0
    print(f"  Evolution took {elapsed:.1f}s ({elapsed/60:.1f} min)", flush=True)

    # ── [4/6] Dedup ──────────────────────────────────────────────────────────
    print("\n[4/6] Correlation dedup...", flush=True)
    unique = correlation_dedup(results, train_windows[0], FS,
                               threshold=0.95, max_keep=50)
    print(f"  {len(unique)} unique features", flush=True)

    # ── [5/6] AUC evaluation ─────────────────────────────────────────────────
    print("\n[5/6] AUC evaluation...", flush=True)

    def get_auc_gp(tree, smry, h_windows, f_windows, fs):
        h_m = evaluate_multi(tree, h_windows, fs)
        f_m = evaluate_multi(tree, f_windows, fs)
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

    gp_rows = []
    for i, (comp, smry, rel, tree) in enumerate(unique):
        r = {'rank': i+1, 'comp': comp, 'smry': smry, 'rel': rel,
             'expr': tree.expr_str(), 'depth': tree.depth()}
        for fname in FAULTS:
            r[f'auc_{fname}'] = get_auc_gp(tree, smry, held_windows,
                                            fault_windows[fname], FS)
        r['auc_mean'] = np.nanmean([r[f'auc_{fname}'] for fname in FAULTS])
        gp_rows.append(r)

    # ── [6/6] Results ────────────────────────────────────────────────────────
    print(f"\n[6/6] RESULTS", flush=True)
    print("=" * 70)

    # Top GP features
    print(f"\n  TOP 15 GP-DISCOVERED FEATURES:")
    print(f"  {'#':>3} {'Comp':>6} {'Rel':>5} {'AUC':>6} {'D':>2}  Expression")
    print("  " + "-" * 70)
    for r in gp_rows[:15]:
        print(f"  {r['rank']:3d} {r['comp']:.4f} {r['rel']:.3f} "
              f"{r['auc_mean']:.4f} L{r['depth']}  {r['expr'][:45]}")

    # Per-fault comparison: best GP vs best baseline
    print(f"\n  HEAD-TO-HEAD: Best GP Feature vs Best Baseline Feature")
    print(f"  {'Fault':<10} {'GP AUC':>7} {'GP Feature':<30} {'BL AUC':>7} {'BL Feature':<15}")
    print("  " + "-" * 75)
    for fname in FAULTS:
        # Best GP for this fault
        gp_sorted = sorted(gp_rows, key=lambda r: -r.get(f'auc_{fname}', 0))
        gp_best_auc = gp_sorted[0][f'auc_{fname}']
        gp_best_expr = gp_sorted[0]['expr'][:28]

        # Best baseline for this fault
        bl_sorted = sorted(baseline_aucs.items(),
                          key=lambda x: -x[1].get(fname, 0))
        bl_best_feat = bl_sorted[0][0]
        bl_best_auc = bl_sorted[0][1][fname]

        winner = "GP" if gp_best_auc > bl_best_auc else "BL" if bl_best_auc > gp_best_auc else "TIE"
        print(f"  {fname:<10} {gp_best_auc:7.3f} {gp_best_expr:<30} "
              f"{bl_best_auc:7.3f} {bl_best_feat:<15} [{winner}]")

    # Overall comparison
    print(f"\n  OVERALL COMPARISON:")
    gp_mean_aucs = [r['auc_mean'] for r in gp_rows]
    bl_mean_aucs = [np.mean(list(v.values())) for v in baseline_aucs.values()]

    gp_top5_mean = np.mean(sorted(gp_mean_aucs, reverse=True)[:5])
    bl_top5_mean = np.mean(sorted(bl_mean_aucs, reverse=True)[:5])

    gp_pct_good = sum(1 for a in gp_mean_aucs if a > 0.70) / len(gp_mean_aucs) * 100
    bl_pct_good = sum(1 for a in bl_mean_aucs if a > 0.70) / len(bl_mean_aucs) * 100

    print(f"  Top-5 mean AUC:  GP={gp_top5_mean:.3f}  Baseline={bl_top5_mean:.3f}")
    print(f"  % good (>0.70):  GP={gp_pct_good:.0f}%  Baseline={bl_pct_good:.0f}%")

    # Reliability distribution
    rels = [r['rel'] for r in gp_rows]
    high_rel = sum(1 for r in rels if r > 0.9)
    print(f"  GP reliability:  {high_rel}/{len(rels)} features with R > 0.9")
    print(f"  GP top-10 rel:   {np.mean(sorted(rels, reverse=True)[:10]):.3f}")

    # GP features per fault type
    print(f"\n  GP FEATURES: PER-FAULT AUC (top 5):")
    print(f"  {'#':>3} ", end='')
    for fname in FAULTS:
        short = fname.replace('_', '')[:5]
        print(f"{short:>7}", end='')
    print(f"  {'mean':>6}  {'Rel':>4}  Expression")
    print("  " + "-" * 100)
    for r in gp_rows[:5]:
        print(f"  {r['rank']:3d} ", end='')
        for fname in FAULTS:
            print(f"{r.get(f'auc_{fname}', 0):7.3f}", end='')
        print(f"  {r['auc_mean']:.3f}  {r['rel']:.2f}  {r['expr'][:30]}")

    print("\n" + "=" * 70)
    print("  SUMMARY: CWRU Real-World Experiment")
    print("=" * 70)
    print(f"  GP evolved {len(unique)} unique features from normal data ONLY")
    print(f"  GP top-5 mean AUC: {gp_top5_mean:.3f} vs Baseline: {bl_top5_mean:.3f}")
    print(f"  GP % good: {gp_pct_good:.0f}% vs Baseline: {bl_pct_good:.0f}%")
    print(f"  Best GP feature: {gp_rows[0]['expr'][:50]}")
    print(f"  The GP never saw any fault data during evolution.")
    print("=" * 70)
    print("  Done.")
