"""
IMS Bearing Run-to-Failure Experiment
=====================================
NASA/IMS dataset: 4 bearings, 2000 RPM, 6000 lbs radial load.
Test 2: ~984 snapshots over 31 days. Bearing 1 develops outer race fault.

Key question: Can GP features trained on early healthy data detect
degradation EARLIER than standard hand-crafted baselines?

Design:
  - Each snapshot: 1 second @ 20kHz = 20,480 points, 4 channels
  - Train GP on first N_TRAIN snapshots (all bearings healthy)
  - Track all features across the full timeline
  - Compare early-detection capability: GP vs baselines
  - "Detection" = z-score > THRESHOLD relative to healthy baseline
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

# ── Data Loading ────────────────────────────────────────────────────────────
IMS_DIR = os.path.join(os.path.dirname(__file__), 'ims_data')
FS = 20000  # 20kHz
SNAPSHOT_LEN = 20480  # 1 second

# Training: first N_TRAIN snapshots (all bearings healthy in early period)
N_TRAIN = 100
# Detection threshold (z-score standard deviations)
THRESHOLD = 5.0
# Minimum consecutive detections to confirm
MIN_CONSEC = 3

def find_test2_dir():
    """Find the Test 2 directory (2nd_test) in the extracted data."""
    path = os.path.join(IMS_DIR, '2nd_test')
    if os.path.isdir(path):
        return path
    raise FileNotFoundError(f"Cannot find 2nd_test directory in {IMS_DIR}")

def load_snapshot(filepath):
    """Load one IMS snapshot file. Returns array of shape (20480, 4)."""
    try:
        data = np.loadtxt(filepath)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        return data
    except Exception as e:
        return None

def load_all_snapshots(test_dir, max_files=None):
    """Load all snapshots from Test 2, sorted chronologically."""
    files = sorted([f for f in os.listdir(test_dir)
                    if not f.startswith('.') and not f.endswith('.zip')])
    if max_files:
        files = files[:max_files]

    snapshots = []
    filenames = []
    for i, fname in enumerate(files):
        path = os.path.join(test_dir, fname)
        if os.path.isdir(path):
            continue
        data = load_snapshot(path)
        if data is not None and len(data) >= 1000:
            snapshots.append(data)
            filenames.append(fname)
            if i % 200 == 0 and i > 0:
                print(f"    Loaded {i}/{len(files)} snapshots...", flush=True)

    return snapshots, filenames

# ── Vibration Type System ───────────────────────────────────────────────────
TYPES = ('V', 'E', 'S')
# All 4 bearings as terminals — GP can discover cross-bearing features
TERMINALS = {'B1': 'V', 'B2': 'V', 'B3': 'V', 'B4': 'V'}
CONSTS = [0.5, 1.0, 2.0, 5.0]

# ── Operators ───────────────────────────────────────────────────────────────
OPS = []
OPS.append(('ddt', 1, ('V',), 'V', lambda a, fs: np.gradient(a, 1/fs)))
OPS.append(('ddt', 1, ('E',), 'E', lambda a, fs: np.gradient(a, 1/fs)))
OPS.append(('abs', 1, ('V',), 'V', lambda a, fs: np.abs(a)))
OPS.append(('abs', 1, ('E',), 'E', lambda a, fs: np.abs(a)))
OPS.append(('neg', 1, ('V',), 'V', lambda a, fs: -a))
OPS.append(('neg', 1, ('S',), 'S', lambda a, fs: -a))
OPS.append(('square', 1, ('V',), 'E', lambda a, fs: a**2))
OPS.append(('square', 1, ('S',), 'S', lambda a, fs: a**2))

def _rstd(a, fs, w=200):  # 200 samples = 10ms at 20kHz
    return pd.Series(a).rolling(w, min_periods=w//2).std().fillna(0).values

OPS.append(('rstd', 1, ('V',), 'V', lambda a, fs: _rstd(a, fs)))
OPS.append(('rstd', 1, ('E',), 'E', lambda a, fs: _rstd(a, fs)))
OPS.append(('env', 1, ('V',), 'V', lambda a, fs: np.abs(a)))

for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))
OPS.append(('div', 2, ('V', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))

UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1]==1 and o[2]==(t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1]==2 and o[2]==key]

# ── Expression Tree ─────────────────────────────────────────────────────────
class Node:
    __slots__ = ('kind', 'name', 'out_type', 'func', 'children', 'const_val')
    def __init__(self, kind, name, out_type, func=None, children=None, const_val=None):
        self.kind = kind; self.name = name; self.out_type = out_type
        self.func = func; self.children = children or []; self.const_val = const_val

    def depth(self):
        if not self.children: return 0
        return 1 + max(c.depth() for c in self.children)

    def size(self):
        return 1 + sum(c.size() for c in self.children)

    def evaluate(self, signals, fs):
        try:
            if self.kind == 'terminal': return signals[self.name].copy()
            elif self.kind == 'const': return np.full(len(signals['B1']), self.const_val)
            elif self.kind == 'unary':
                return self.func(self.children[0].evaluate(signals, fs), fs)
            elif self.kind == 'binary':
                left = self.children[0].evaluate(signals, fs)
                right = self.children[1].evaluate(signals, fs)
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except: return None

    def expr_str(self):
        if self.kind == 'terminal': return self.name
        elif self.kind == 'const': return f"{self.const_val:.2f}"
        elif self.kind == 'unary': return f"{self.name}({self.children[0].expr_str()})"
        elif self.kind == 'binary':
            return f"({self.children[0].expr_str()} {self.name} {self.children[1].expr_str()})"

    def copy(self): return deepcopy(self)

# ── Tree ops ────────────────────────────────────────────────────────────────
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
    if max_depth <= 0: return random_terminal(target_type)
    if random.random() < 0.3:
        t = random_terminal(target_type)
        if target_type is None or t.out_type == target_type: return t
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
                par1.children[idx1] = n2; par2.children[idx2] = n1
                return t1, t2
    return t1, t2

def simplify(tree):
    tree = tree.copy()
    if tree.kind == 'unary' and tree.children:
        tree.children[0] = simplify(tree.children[0])
        child = tree.children[0]
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'abs': return child
        if tree.name == 'neg' and child.kind == 'unary' and child.name == 'neg': return child.children[0]
    if tree.kind == 'binary' and len(tree.children) == 2:
        tree.children[0] = simplify(tree.children[0])
        tree.children[1] = simplify(tree.children[1])
    return tree

def is_rstd_root(tree):
    if tree.kind == 'unary' and tree.name == 'rstd': return True
    if tree.kind == 'binary' and tree.name == 'mul':
        for i in range(2):
            if tree.children[i].kind == 'const' and is_rstd_root(tree.children[1-i]): return True
    if tree.kind == 'unary' and tree.name in ('neg', 'abs'):
        return is_rstd_root(tree.children[0])
    return False

RSTD_PENALTY = 0.5

# ── Split-half & Fitness ───────────────────────────────────────────────────
SUMMARIES = {
    'std': lambda a: np.std(a),
    'rms': lambda a: np.sqrt(np.mean(a**2)),
    'kurt': lambda a: float(stats.kurtosis(a, fisher=True)) if len(a) > 10 else 0.0,
}

def snapshot_to_signals(snapshot):
    """Convert a (20480, 4) array to signal dict."""
    n_cols = snapshot.shape[1] if snapshot.ndim > 1 else 1
    signals = {}
    if n_cols >= 1: signals['B1'] = snapshot[:, 0] if n_cols > 1 else snapshot.flatten()
    if n_cols >= 2: signals['B2'] = snapshot[:, 1]
    if n_cols >= 3: signals['B3'] = snapshot[:, 2]
    if n_cols >= 4: signals['B4'] = snapshot[:, 3]
    # Fill missing with zeros
    for k in ['B1', 'B2', 'B3', 'B4']:
        if k not in signals:
            signals[k] = np.zeros(SNAPSHOT_LEN)
    return signals

def evaluate_multi(tree, windows, fs):
    """Evaluate tree on multiple snapshot windows."""
    results = {k: [] for k in SUMMARIES}
    for w in windows:
        val = tree.evaluate(w, fs)
        if val is None: return None
        finite = val[np.isfinite(val)]
        if len(finite) < 100: return None
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
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8: stationarity = 1.0
    else: stationarity = max(0.0, 1.0 - np.std(cmeans) / (abs(np.mean(cmeans)) + 1e-8))
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))
    return (tightness + stationarity + headroom + reliability) / 4.0

def split_snapshot_signals(signals):
    """Split each signal in half for reliability computation."""
    mid = SNAPSHOT_LEN // 2
    first = {k: v[:mid] for k, v in signals.items()}
    second = {k: v[mid:] for k, v in signals.items()}
    return first, second

def evaluate_multi_halves(tree, windows, fs):
    """Evaluate on first and second halves of each window."""
    first_results = {k: [] for k in SUMMARIES}
    second_results = {k: [] for k in SUMMARIES}
    for w in windows:
        fst, snd = split_snapshot_signals(w)
        for half, results in [(fst, first_results), (snd, second_results)]:
            val = tree.evaluate(half, fs)
            if val is None: return None, None
            finite = val[np.isfinite(val)]
            if len(finite) < 50: return None, None
            for sn, sf in SUMMARIES.items():
                try: results[sn].append(sf(finite))
                except: results[sn].append(np.nan)
    first_out = {k: np.array(v) for k, v in first_results.items()}
    second_out = {k: np.array(v) for k, v in second_results.items()}
    return first_out, second_out

def fitness_v5(tree, train_signals, fs):
    full_multi = evaluate_multi(tree, train_signals, fs)
    if full_multi is None: return 0.0, 'none', 0.0
    first_multi, second_multi = evaluate_multi_halves(tree, train_signals, fs)
    best_score, best_summary, best_rel = 0.0, 'none', 0.0
    for sname in SUMMARIES:
        full_vals = full_multi[sname]
        if first_multi is not None and second_multi is not None:
            rel = compute_reliability(first_multi[sname], second_multi[sname])
        else: rel = 0.0
        sc = composite_v5(full_vals, rel)
        if sc > best_score:
            best_score, best_summary, best_rel = sc, sname, rel
    if is_rstd_root(tree): best_score *= RSTD_PENALTY
    return best_score, best_summary, best_rel

# ── Diversity & Dedup ──────────────────────────────────────────────────────
def expr_hash(tree, precision=2):
    return tree.expr_str()[:precision * 10]

def apply_diversity_penalty(scores, population):
    hashes = [expr_hash(t) for t in population]
    counts = Counter(hashes)
    return [s / counts[h] for s, h in zip(scores, hashes)]

def correlation_dedup(candidates, ref_signals, fs, threshold=0.95, max_keep=50):
    kept = []; kept_outputs = []
    for score, smry, rel, tree in candidates:
        if score < 0.01: continue
        val = tree.evaluate(ref_signals, fs)
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

# ── GP Evolution ───────────────────────────────────────────────────────────
def run_gp(train_signals, fs, pop_size=200, generations=80, max_depth=4,
           tournament_k=5, elite_frac=0.05):

    print("  Initializing population...", flush=True)
    population = [simplify(random_tree(max_depth)) for _ in range(pop_size)]
    raw_scores = []; best_sums = []; reliabilities = []
    for t in population:
        sc, sm, rel = fitness_v5(t, train_signals, fs)
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
                sc1, sm1, rl1 = fitness_v5(c1, train_signals, fs)
                new_pop.append(c1); new_raw.append(sc1); new_sm.append(sm1); new_rel.append(rl1)
                if len(new_pop) < pop_size:
                    c2 = simplify(c2)
                    sc2, sm2, rl2 = fitness_v5(c2, train_signals, fs)
                    new_pop.append(c2); new_raw.append(sc2); new_sm.append(sm2); new_rel.append(rl2)
            elif r < 0.90:
                child = simplify(mutate(tournament(), max_depth))
                sc, sm, rl = fitness_v5(child, train_signals, fs)
                new_pop.append(child); new_raw.append(sc); new_sm.append(sm); new_rel.append(rl)
            else:
                imm = simplify(random_tree(max_depth))
                sc, sm, rl = fitness_v5(imm, train_signals, fs)
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

# ── Baseline Features ──────────────────────────────────────────────────────
def extract_baseline(signals):
    """Standard vibration features for each bearing."""
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
    # Cross-bearing
    if 'B1' in signals and 'B2' in signals:
        f['B1B2_ratio'] = (np.sqrt(np.mean(signals['B1']**2)) /
                           (np.sqrt(np.mean(signals['B2']**2)) + 1e-8))
    if 'B1' in signals and 'B3' in signals:
        f['B1B3_ratio'] = (np.sqrt(np.mean(signals['B1']**2)) /
                           (np.sqrt(np.mean(signals['B3']**2)) + 1e-8))
    return f

# ── Timeline Evaluation ───────────────────────────────────────────────────
def compute_feature_timeline(tree, smry, all_signals, fs):
    """Compute a GP feature's summary value across all snapshots."""
    timeline = []
    sfunc = SUMMARIES.get(smry, SUMMARIES['std'])
    for signals in all_signals:
        val = tree.evaluate(signals, fs)
        if val is not None:
            finite = val[np.isfinite(val)]
            if len(finite) > 100:
                timeline.append(sfunc(finite))
            else:
                timeline.append(np.nan)
        else:
            timeline.append(np.nan)
    return np.array(timeline)

def find_first_detection(timeline, healthy_mean, healthy_std, threshold, min_consec):
    """Find first index where z-score exceeds threshold for min_consec consecutive snapshots."""
    if healthy_std < 1e-10: healthy_std = 1e-10
    z_scores = np.abs(timeline - healthy_mean) / healthy_std
    count = 0
    for i, z in enumerate(z_scores):
        if np.isfinite(z) and z > threshold:
            count += 1
            if count >= min_consec:
                return i - min_consec + 1  # return start of the run
        else:
            count = 0
    return len(timeline)  # never detected

# ── MAIN ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':

    print("=" * 80)
    print("  IMS BEARING RUN-TO-FAILURE EXPERIMENT")
    print("  Train on early healthy data | Track degradation over time")
    print("  Test 2: Bearing 1 outer race failure | 4 channels @ 20kHz")
    print("=" * 80)

    # ── [1/7] Load data ─────────────────────────────────────────────────────
    print("\n[1/7] Loading IMS Test 2 data...", flush=True)
    t0 = time.time()

    test2_dir = find_test2_dir()
    print(f"  Found: {test2_dir}")

    snapshots, filenames = load_all_snapshots(test2_dir)
    print(f"  {len(snapshots)} snapshots loaded in {time.time()-t0:.1f}s")
    print(f"  Shape: {snapshots[0].shape}")
    print(f"  First: {filenames[0]}")
    print(f"  Last:  {filenames[-1]}")

    # Convert to signal dicts
    all_signals = [snapshot_to_signals(s) for s in snapshots]

    # ── [2/7] GP training data (early healthy period) ───────────────────────
    print(f"\n[2/7] Preparing training data (first {N_TRAIN} snapshots)...", flush=True)
    train_signals = all_signals[:N_TRAIN]
    print(f"  Training on snapshots 0-{N_TRAIN-1} ({filenames[0]} to {filenames[N_TRAIN-1]})")

    # Quick sanity: check B1 RMS over time for training period
    train_rms = [np.sqrt(np.mean(s['B1']**2)) for s in train_signals]
    print(f"  B1 RMS during training: mean={np.mean(train_rms):.6f} "
          f"std={np.std(train_rms):.6f} cv={np.std(train_rms)/(np.mean(train_rms)+1e-8):.3f}")

    # ── [3/7] Baseline features over full timeline ──────────────────────────
    print(f"\n[3/7] Computing baseline features over full timeline...", flush=True)
    t0 = time.time()

    baseline_timeline = pd.DataFrame([extract_baseline(s) for s in all_signals])
    b_feats = [c for c in baseline_timeline.columns if c.startswith('B1')]
    # Add cross-bearing features
    b_feats += [c for c in baseline_timeline.columns if 'ratio' in c]

    # Healthy baseline stats (from training period)
    bl_train = baseline_timeline.iloc[:N_TRAIN]
    bl_mu  = bl_train.mean()
    bl_sig = bl_train.std().clip(lower=1e-10)

    print(f"  {len(b_feats)} baseline features computed in {time.time()-t0:.1f}s")

    # Find baseline detection times
    print(f"\n  BASELINE DETECTION TIMES (threshold={THRESHOLD}sigma, consec={MIN_CONSEC}):")
    bl_detections = {}
    for feat in b_feats:
        tl = baseline_timeline[feat].values
        det_idx = find_first_detection(tl, bl_mu[feat], bl_sig[feat], THRESHOLD, MIN_CONSEC)
        det_pct = det_idx / len(tl) * 100
        bl_detections[feat] = det_idx
        if det_idx < len(tl):
            print(f"  {feat:<15} detects at snapshot {det_idx:5d}/{len(tl)} "
                  f"({det_pct:5.1f}% through life)")
        else:
            print(f"  {feat:<15} NEVER DETECTS")

    earliest_bl = min(bl_detections.values())
    earliest_bl_feat = min(bl_detections, key=bl_detections.get)
    print(f"\n  Earliest baseline detection: {earliest_bl_feat} at snapshot {earliest_bl} "
          f"({earliest_bl/len(all_signals)*100:.1f}%)")

    # ── [4/7] GP Evolution ──────────────────────────────────────────────────
    print(f"\n[4/7] GP Evolution on early healthy data...", flush=True)
    t0 = time.time()
    results = run_gp(train_signals, FS)
    elapsed = time.time() - t0
    print(f"  Evolution took {elapsed:.1f}s ({elapsed/60:.1f} min)", flush=True)

    # ── [5/7] Dedup ─────────────────────────────────────────────────────────
    print(f"\n[5/7] Correlation dedup...", flush=True)
    unique = correlation_dedup(results, train_signals[0], FS, threshold=0.95, max_keep=50)
    print(f"  {len(unique)} unique features", flush=True)

    # ── [6/7] Timeline evaluation for GP features ──────────────────────────
    print(f"\n[6/7] Computing GP feature timelines...", flush=True)
    t0 = time.time()

    gp_rows = []
    for i, (comp, smry, rel, tree) in enumerate(unique):
        tl = compute_feature_timeline(tree, smry, all_signals, FS)
        # Healthy baseline stats
        h_vals = tl[:N_TRAIN]
        h_mu = np.nanmean(h_vals)
        h_sig = np.nanstd(h_vals)
        det_idx = find_first_detection(tl, h_mu, h_sig, THRESHOLD, MIN_CONSEC)
        det_pct = det_idx / len(tl) * 100

        gp_rows.append({
            'rank': i+1, 'comp': comp, 'smry': smry, 'rel': rel,
            'expr': tree.expr_str(), 'depth': tree.depth(),
            'det_idx': det_idx, 'det_pct': det_pct,
            'timeline': tl, 'h_mu': h_mu, 'h_sig': h_sig,
        })

    print(f"  Done in {time.time()-t0:.1f}s", flush=True)

    # ── [7/7] Results ──────────────────────────────────────────────────────
    print(f"\n[7/7] RESULTS", flush=True)
    print("=" * 80)

    n_total = len(all_signals)

    # GP detection times
    print(f"\n  GP FEATURE DETECTION TIMES (top 20 by earliest detection):")
    print(f"  {'#':>3} {'Comp':>6} {'Rel':>5} {'DetSnap':>8} {'DetPct':>6} {'D':>2}  Expression")
    print("  " + "-" * 75)
    gp_by_det = sorted(gp_rows, key=lambda r: r['det_idx'])
    for r in gp_by_det[:20]:
        det_str = f"{r['det_idx']}" if r['det_idx'] < n_total else "NEVER"
        pct_str = f"{r['det_pct']:.1f}%" if r['det_idx'] < n_total else "---"
        print(f"  {r['rank']:3d} {r['comp']:.4f} {r['rel']:.3f} "
              f"{det_str:>8} {pct_str:>6} L{r['depth']}  {r['expr'][:40]}")

    # Head-to-head: earliest GP vs earliest baseline
    earliest_gp_row = gp_by_det[0]
    earliest_gp = earliest_gp_row['det_idx']

    print(f"\n  EARLY DETECTION HEAD-TO-HEAD:")
    print(f"  {'Method':<12} {'Feature':<35} {'Detect@':>8} {'% Life':>7}")
    print("  " + "-" * 65)
    print(f"  {'GP':.<12} {earliest_gp_row['expr'][:33]:<35} "
          f"{earliest_gp:>8} {earliest_gp/n_total*100:>6.1f}%")
    print(f"  {'Baseline':.<12} {earliest_bl_feat:<35} "
          f"{earliest_bl:>8} {earliest_bl/n_total*100:>6.1f}%")

    if earliest_gp < earliest_bl:
        lead = earliest_bl - earliest_gp
        lead_hrs = lead * 10 / 60  # 10 min between snapshots
        print(f"\n  >>> GP DETECTS {lead} SNAPSHOTS EARLIER ({lead_hrs:.1f} hours)")
    elif earliest_gp > earliest_bl:
        lag = earliest_gp - earliest_bl
        lag_hrs = lag * 10 / 60
        print(f"\n  >>> BASELINE DETECTS {lag} SNAPSHOTS EARLIER ({lag_hrs:.1f} hours)")
    else:
        print(f"\n  >>> TIE: both detect at same time")

    # Detection rate comparison
    gp_detected = sum(1 for r in gp_rows if r['det_idx'] < n_total)
    bl_detected = sum(1 for f, idx in bl_detections.items() if idx < n_total)
    print(f"\n  Detection rate: GP {gp_detected}/{len(gp_rows)} | "
          f"Baseline {bl_detected}/{len(b_feats)}")

    # Top 5 by composite (not detection time)
    print(f"\n  TOP 5 BY GP COMPOSITE SCORE:")
    print(f"  {'#':>3} {'Comp':>6} {'Rel':>5} {'DetSnap':>8} {'DetPct':>6}  Expression")
    print("  " + "-" * 70)
    for r in gp_rows[:5]:
        det_str = f"{r['det_idx']}" if r['det_idx'] < n_total else "NEVER"
        pct_str = f"{r['det_pct']:.1f}%" if r['det_idx'] < n_total else "---"
        print(f"  {r['rank']:3d} {r['comp']:.4f} {r['rel']:.3f} "
              f"{det_str:>8} {pct_str:>6}  {r['expr'][:40]}")

    # Terminal usage analysis
    print(f"\n  TERMINAL USAGE IN TOP 20 GP FEATURES:")
    for bname in ['B1', 'B2', 'B3', 'B4']:
        count = sum(1 for r in gp_by_det[:20] if bname in r['expr'])
        print(f"  {bname}: appears in {count}/20 features")
    cross = sum(1 for r in gp_by_det[:20]
                if ('B1' in r['expr'] and any(b in r['expr'] for b in ['B2', 'B3', 'B4'])))
    print(f"  Cross-bearing (B1 + other): {cross}/20 features")

    print("\n" + "=" * 80)
    print("  SUMMARY: IMS Run-to-Failure Experiment")
    print("=" * 80)
    print(f"  {n_total} snapshots over ~{n_total*10/60/24:.0f} days")
    print(f"  GP trained on first {N_TRAIN} snapshots (early healthy)")
    print(f"  GP earliest detection: snapshot {earliest_gp} ({earliest_gp/n_total*100:.1f}%)")
    print(f"  Baseline earliest: snapshot {earliest_bl} ({earliest_bl/n_total*100:.1f}%)")
    if earliest_gp < earliest_bl:
        print(f"  GP WINS: detects {(earliest_bl-earliest_gp)*10/60:.1f} hours earlier")
    elif earliest_gp > earliest_bl:
        print(f"  Baseline wins: detects {(earliest_gp-earliest_bl)*10/60:.1f} hours earlier")
    print(f"  {len(unique)} unique GP features | {gp_detected} detect degradation")
    print("=" * 80)
    print("  Done.")
