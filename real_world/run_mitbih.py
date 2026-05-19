"""
MIT-BIH Arrhythmia Database GP Experiment -- ECG Anomaly Detection
==================================================================
48 patients, 2-channel ECG at 360 Hz, beat-level cardiologist annotations.

Key question: Can GP features trained ONLY on normal heartbeats detect
arrhythmias WITHOUT any labeled arrhythmia examples?

Design:
  - Extract beat windows (250 samples, ~694ms) centered on annotated R-peaks
  - Train GP on pooled normal ('N') beats from all patients
  - Per-patient evaluation: compute feature for each beat, set threshold
    from that patient's own normal distribution, compute AUC
  - Compare GP vs standard expert-crafted ECG morphological features
  - Report per-patient and aggregate results

Type system (ECG electrophysiology):
  V  = Voltage (mV) — raw ECG amplitude
  E  = Energy — squared / power quantities
  S  = Scalar — dimensionless derived quantities

Requires: pip install wfdb numpy scipy scikit-learn
Data downloaded automatically from PhysioNet via wfdb.
"""
import numpy as np
from scipy import stats
from sklearn.metrics import roc_auc_score
from collections import Counter
import random, sys, os, time, warnings

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

try:
    import wfdb
except ImportError:
    print("ERROR: wfdb not installed.  Run:  pip install wfdb")
    sys.exit(1)

np.random.seed(42)
random.seed(42)

# -- Parameters ---------------------------------------------------------------
FS = 360              # MIT-BIH sampling rate (Hz)
BEAT_BEFORE = 100     # samples before R-peak
BEAT_AFTER = 150      # samples after R-peak
BEAT_LEN = BEAT_BEFORE + BEAT_AFTER  # 250 samples = ~694ms

N_TRAIN = 120         # normal beats for GP training
RSTD_WINDOW = 25      # rolling window (~70ms ≈ QRS width)

# GP parameters
POP_SIZE = 200
GENERATIONS = 60
MAX_DEPTH = 4
TOURNAMENT_K = 5
ELITE_FRAC = 0.05
CROSSOVER_RATE = 0.60
MUTATION_RATE = 0.30
IMMIGRATION_RATE = 0.15

# Beat classification
NORMAL_SYMBOLS = {'N'}
ARRHYTHMIA_SYMBOLS = {'L', 'R', 'V', 'A', 'a', 'J', 'S', 'E', 'F'}
MIN_ARRHYTHMIA = 10   # minimum arrhythmia beats for AUC evaluation

# MIT-BIH record numbers
RECORDS = [
    '100', '101', '102', '103', '104', '105', '106', '107', '108', '109',
    '111', '112', '113', '114', '115', '116', '117', '118', '119',
    '121', '122', '123', '124',
    '200', '201', '202', '203', '205', '207', '208', '209', '210',
    '211', '212', '213', '214', '215', '217', '219', '220', '221',
    '222', '223', '228', '230', '231', '232', '233', '234',
]

print("=" * 80)
print("  MIT-BIH Arrhythmia Database GP Experiment -- ECG Anomaly Detection")
print("  Train on normal heartbeats only | Detect arrhythmias unsupervised")
print("  48 patients, 2-channel ECG, 360 Hz, beat-level annotations")
print("=" * 80)

# =============================================================================
# [1] Load data via wfdb
# =============================================================================
print(f"\n[1] Loading MIT-BIH records from PhysioNet (first run downloads ~100 MB)...")
t_start = time.time()

patients = {}
total_normal = 0
total_arrhythmia = 0

for rec_id in RECORDS:
    try:
        record = wfdb.rdrecord(rec_id, pn_dir='mitdb')
        ann = wfdb.rdann(rec_id, 'atr', pn_dir='mitdb')
    except Exception as e:
        print(f"    Skipping record {rec_id}: {e}")
        continue

    sig = record.p_signal          # (n_samples, n_channels)
    n_ch = sig.shape[1]
    leads = record.sig_name        # e.g. ['MLII', 'V5']

    normal_beats, arrhythmia_beats, arr_types = [], [], []

    for sample, symbol in zip(ann.sample, ann.symbol):
        if symbol not in NORMAL_SYMBOLS and symbol not in ARRHYTHMIA_SYMBOLS:
            continue
        start = sample - BEAT_BEFORE
        end = sample + BEAT_AFTER
        if start < 0 or end > len(sig):
            continue
        window = {
            'ECG1': sig[start:end, 0].copy(),
            'ECG2': sig[start:end, min(1, n_ch - 1)].copy(),
            'fs': FS,
        }
        if symbol in NORMAL_SYMBOLS:
            normal_beats.append(window)
        else:
            arrhythmia_beats.append(window)
            arr_types.append(symbol)

    if len(normal_beats) < 20:
        continue

    patients[rec_id] = {
        'normal': normal_beats,
        'arrhythmia': arrhythmia_beats,
        'arrhythmia_types': arr_types,
        'leads': leads,
    }
    total_normal += len(normal_beats)
    total_arrhythmia += len(arrhythmia_beats)

print(f"    Loaded {len(patients)} patients in {time.time() - t_start:.1f}s")
print(f"    Total: {total_normal:,} normal beats, {total_arrhythmia:,} arrhythmia beats")

eval_patients = {k: v for k, v in patients.items()
                 if len(v['arrhythmia']) >= MIN_ARRHYTHMIA}
print(f"    {len(eval_patients)} patients with >= {MIN_ARRHYTHMIA} arrhythmia beats")

# Arrhythmia breakdown
all_arr = []
for p in patients.values():
    all_arr.extend(p['arrhythmia_types'])
arr_counts = Counter(all_arr)
print(f"    Arrhythmia types: {dict(arr_counts.most_common(8))}")

# =============================================================================
# [2] Create GP training set
# =============================================================================
print(f"\n[2] Creating GP training set ({N_TRAIN} normal beats, pooled)...")

# Sample evenly across patients for diversity
beats_per_patient = max(2, N_TRAIN // len(patients))
all_normal = []
for pid, pdata in patients.items():
    pbeats = pdata['normal'][:]
    random.shuffle(pbeats)
    all_normal.extend(pbeats[:beats_per_patient])

random.shuffle(all_normal)
train_beats = all_normal[:N_TRAIN]
print(f"    Selected {len(train_beats)} beats (~{beats_per_patient}/patient, "
      f"{len(patients)} patients)")

# =============================================================================
# Type System (ECG electrophysiology)
# =============================================================================
TYPES = ('V', 'E', 'S')     # Voltage, Energy, Scalar

TERMINALS = {'ECG1': 'V', 'ECG2': 'V'}
FIRST_SIGNAL = 'ECG1'
CONSTS = [0.5, 1.0, 2.0, 5.0]

# -- Operators ----------------------------------------------------------------
OPS = []

# Temporal derivative (captures QRS slopes, P/T-wave morphology)
OPS.append(('ddt',  1, ('V',), 'V', lambda a, fs: np.gradient(a, 1.0 / fs)))
OPS.append(('ddt',  1, ('E',), 'E', lambda a, fs: np.gradient(a, 1.0 / fs)))

# Shape operators
for t in TYPES:
    OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))
OPS.append(('neg', 1, ('V',), 'V', lambda a, fs: -a))
OPS.append(('neg', 1, ('S',), 'S', lambda a, fs: -a))

# Voltage squared = energy (instantaneous power)
OPS.append(('square', 1, ('V',), 'E', lambda a, fs: a ** 2))
OPS.append(('square', 1, ('E',), 'E', lambda a, fs: np.clip(a ** 2, 0, 1e12)))  # 4th-order
OPS.append(('square', 1, ('S',), 'S', lambda a, fs: a ** 2))


def _rstd(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w:
        return np.zeros(n)
    cs  = np.concatenate([[0], np.cumsum(a)])
    cs2 = np.concatenate([[0], np.cumsum(a ** 2)])
    s   = cs[w:] - cs[:-w]
    s2  = cs2[w:] - cs2[:-w]
    var = np.maximum(0, s2 / w - (s / w) ** 2)
    out = np.zeros(n)
    out[w - 1:] = np.sqrt(var)
    return out


def _rmean(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w:
        return a.copy()
    cs  = np.concatenate([[0], np.cumsum(a)])
    out = np.zeros(n)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


# Rolling statistics (local morphological variation within a beat)
OPS.append(('rstd',  1, ('V',), 'V', lambda a, fs: _rstd(a, fs)))
OPS.append(('rstd',  1, ('E',), 'E', lambda a, fs: _rstd(a, fs)))
OPS.append(('rmean', 1, ('V',), 'V', lambda a, fs: _rmean(a, fs)))
OPS.append(('rmean', 1, ('E',), 'E', lambda a, fs: _rmean(a, fs)))

# Binary same-type
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Cross-type products
OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))   # cross-lead energy
OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))

# Ratios
OPS.append(('div', 2, ('V', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))  # lead ratio
OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))

# Build lookup tables
UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1] == 1 and o[2] == (t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1] == 2 and o[2] == key]

n_ops = sum(1 for o in OPS)
print(f"    Type system: {len(TYPES)} types, {len(TERMINALS)} terminals, {n_ops} operators")

# =============================================================================
# Expression Tree
# =============================================================================
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
        if not self.children:
            return 0
        return 1 + max(c.depth() for c in self.children)

    def size(self):
        return 1 + sum(c.size() for c in self.children)

    def evaluate(self, signals, fs, _depth=0):
        if _depth > 10:
            return None
        try:
            if self.kind == 'terminal':
                return signals[self.name].copy()
            elif self.kind == 'const':
                return np.full(len(signals[FIRST_SIGNAL]), self.const_val)
            elif self.kind == 'unary':
                child_val = self.children[0].evaluate(signals, fs, _depth + 1)
                if child_val is None:
                    return None
                return self.func(child_val, fs)
            elif self.kind == 'binary':
                left  = self.children[0].evaluate(signals, fs, _depth + 1)
                right = self.children[1].evaluate(signals, fs, _depth + 1)
                if left is None or right is None:
                    return None
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except:
            return None

    def expr_str(self):
        if self.kind == 'terminal':
            return self.name
        elif self.kind == 'const':
            return f"{self.const_val:.1f}"
        elif self.kind == 'unary':
            return f"{self.name}({self.children[0].expr_str()})"
        elif self.kind == 'binary':
            return f"({self.children[0].expr_str()} {self.name} {self.children[1].expr_str()})"

    def copy(self):
        new = Node(self.kind, self.name, self.out_type, self.func, None, self.const_val)
        new.children = [c.copy() for c in self.children] if self.children else []
        return new


# =============================================================================
# Tree Generation
# =============================================================================
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
        left  = random_tree(max_depth - 1, t1)
        right = random_tree(max_depth - 1, t2)
        return Node('binary', name, out, func, [left, right])
    return random_terminal(target_type)


def random_cross_lead_tree(max_depth=MAX_DEPTH):
    """Generate a tree whose root is a binary op combining V-type children.
    Encourages cross-lead features (ECG1 × ECG2, ECG1 - ECG2, etc.)."""
    vv_ops = [(n, f, o) for n, f, o in BINARY_OPS.get(('V', 'V'), [])]
    if not vv_ops:
        return random_tree(max_depth)
    name, func, out = random.choice(vv_ops)
    left  = random_tree(max_depth - 1, 'V')
    right = random_tree(max_depth - 1, 'V')
    return simplify(Node('binary', name, out, func, [left, right]))


# =============================================================================
# Simplification
# =============================================================================
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
        if child.kind == 'const':
            val = child.const_val
            if tree.name == 'abs':
                return Node('const', f'c{abs(val)}', 'S', const_val=abs(val))
            if tree.name == 'neg':
                return Node('const', f'c{-val}', 'S', const_val=-val)
            if tree.name == 'square':
                return Node('const', f'c{val**2}', 'S', const_val=val**2)
    if tree.kind == 'binary' and len(tree.children) == 2:
        tree.children[0] = simplify(tree.children[0])
        tree.children[1] = simplify(tree.children[1])
        if tree.name == 'mul':
            for i in range(2):
                if (tree.children[i].kind == 'const'
                        and abs(tree.children[i].const_val - 1.0) < 1e-8):
                    return tree.children[1 - i]
            # Fold const * const
            if tree.children[0].kind == 'const' and tree.children[1].kind == 'const':
                v = tree.children[0].const_val * tree.children[1].const_val
                return Node('const', f'c{v}', 'S', const_val=v)
            # Collapse c * (c2 * expr) → (c*c2) * expr
            for i in range(2):
                ci, oi = tree.children[i], tree.children[1 - i]
                if (ci.kind == 'const' and oi.kind == 'binary'
                        and oi.name == 'mul'):
                    for j in range(2):
                        if oi.children[j].kind == 'const':
                            v = ci.const_val * oi.children[j].const_val
                            inner = oi.children[1 - j]
                            if abs(v - 1.0) < 1e-8:
                                return inner
                            cn = Node('const', f'c{v}', 'S', const_val=v)
                            return Node('binary', 'mul', inner.out_type,
                                        tree.func, [cn, inner])
    return tree


# =============================================================================
# Tree Manipulation
# =============================================================================
def _all_nodes(node, path, parent, idx):
    yield node, path, parent, idx
    for i, child in enumerate(node.children):
        yield from _all_nodes(child, path + [i], node, i)


def mutate(tree, max_depth=MAX_DEPTH):
    tree = tree.copy()
    nodes = list(_all_nodes(tree, [], None, None))
    if not nodes:
        return tree
    node, path, parent, idx = random.choice(nodes)
    new_sub = random_tree(max(1, max_depth - len(path)), node.out_type)
    if parent is None:
        return new_sub
    parent.children[idx] = new_sub
    return tree


def crossover(t1, t2, max_depth=MAX_DEPTH):
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
                n1c, n2c = n1.copy(), n2.copy()
                par1.children[idx1] = n2c
                par2.children[idx2] = n1c
                return t1, t2
    return t1, t2


# =============================================================================
# Fitness Function (v5: tightness + stationarity + headroom + reliability)
# =============================================================================
SUMMARIES = {
    'std':      lambda a: np.std(a),
    'rms':      lambda a: np.sqrt(np.mean(a ** 2)),
    'mean_abs': lambda a: np.mean(np.abs(a)),
}


def evaluate_feature(tree, beats, fs, summary='std'):
    sfunc = SUMMARIES[summary]
    scalars = []
    for beat in beats:
        signals = {k: v for k, v in beat.items() if isinstance(v, np.ndarray)}
        result = tree.evaluate(signals, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 10:
            return None
        scalars.append(sfunc(finite))
    arr = np.array(scalars)
    # Filter degenerate features (constant or near-constant output)
    if np.std(arr) < 1e-8:
        return None
    return arr


def split_half_reliability(tree, beats, fs, summary='std'):
    sfunc = SUMMARIES[summary]
    first_half, second_half = [], []
    for beat in beats:
        signals = {k: v for k, v in beat.items() if isinstance(v, np.ndarray)}
        result = tree.evaluate(signals, fs)
        if result is None:
            return 0.0
        finite = result[np.isfinite(result)]
        if len(finite) < 16:
            return 0.0
        mid = len(finite) // 2
        first_half.append(sfunc(finite[:mid]))
        second_half.append(sfunc(finite[mid:]))
    if len(first_half) < 10:
        return 0.0
    if np.std(first_half) < 1e-10 or np.std(second_half) < 1e-10:
        return 0.0
    r = np.corrcoef(first_half, second_half)[0, 1]
    return max(0.0, r) if np.isfinite(r) else 0.0


def composite_v5(vals, reliability):
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0
    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.005:
        return 0.0

    # Tightness (low entropy → tight distribution)
    c, _ = np.histogram(vals, bins=min(20, len(vals) // 3))
    p = c / c.sum()
    p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)

    # Stationarity (stable across time chunks)
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) / (abs(np.mean(cmeans)) + 1e-8))

    # Headroom (low excess kurtosis → room for outlier detection)
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))

    return (tightness + stationarity + headroom + reliability) / 4.0


def fitness_full(tree, train_beats, fs):
    best_score, best_smry, best_rel = 0.0, 'std', 0.0
    for smry in SUMMARIES:
        vals = evaluate_feature(tree, train_beats, fs, smry)
        if vals is None:
            continue
        rel = split_half_reliability(tree, train_beats, fs, smry)
        sc = composite_v5(vals, rel)

        # Anti-gaming: penalise rstd/rmean at root
        penalty = 1.0
        if tree.kind == 'unary' and tree.name in ('rstd', 'rmean'):
            penalty = 0.5
        node = tree
        smooth_depth = 0
        while node.kind == 'unary' and node.name in ('rmean', 'rstd'):
            smooth_depth += 1
            node = node.children[0]
        if smooth_depth >= 2:
            penalty *= 0.5

        sc = sc * penalty * (0.7 if rel < 0.3 else 1.0)

        if sc > best_score:
            best_score, best_smry, best_rel = sc, smry, rel

    return best_score, best_smry, best_rel


# =============================================================================
# Diversity & Deduplication
# =============================================================================
def expr_hash(tree):
    return hash(tree.expr_str()[:60])


def deduplicate(population, scores, beats, fs, threshold=0.85):
    n_check = min(80, len(population))
    vectors = []
    for i in range(n_check):
        v = evaluate_feature(population[i], beats, fs)
        vectors.append(v if v is not None else np.zeros(len(beats)))
    keep = [True] * n_check
    for i in range(1, n_check):
        if not keep[i]:
            continue
        for j in range(i):
            if not keep[j]:
                continue
            vi, vj = vectors[i], vectors[j]
            if (len(vi) == len(vj) and np.std(vi) > 1e-10 and np.std(vj) > 1e-10):
                r = abs(np.corrcoef(vi, vj)[0, 1])
                if np.isfinite(r) and r > threshold:
                    keep[i] = False
                    break
    new_pop = [population[i] for i in range(n_check) if keep[i]]
    new_sc  = [scores[i]     for i in range(n_check) if keep[i]]
    new_pop.extend(population[n_check:])
    new_sc.extend(scores[n_check:])
    return new_pop, new_sc


# =============================================================================
# GP Evolution
# =============================================================================
def run_gp(train_beats, fs):
    print(f"\n[3] Running GP: pop={POP_SIZE}, gen={GENERATIONS}, "
          f"depth<={MAX_DEPTH}, {len(train_beats)} beats")

    # Seed 25% of initial population with cross-lead trees
    n_cross = POP_SIZE // 4
    population = ([random_cross_lead_tree(MAX_DEPTH) for _ in range(n_cross)] +
                  [simplify(random_tree(MAX_DEPTH)) for _ in range(POP_SIZE - n_cross)])
    scores, summaries, reliabilities = [], [], []
    for t in population:
        sc, sm, rl = fitness_full(t, train_beats, fs)
        scores.append(sc)
        summaries.append(sm)
        reliabilities.append(rl)

    n_elite = max(2, int(POP_SIZE * ELITE_FRAC))
    n_immigrant = max(2, int(POP_SIZE * IMMIGRATION_RATE))
    best_ever = (0.0, None, 'std', 0.0)

    t0 = time.time()
    for gen in range(GENERATIONS):
        paired = sorted(zip(scores, summaries, reliabilities, population),
                        key=lambda x: -x[0])

        if paired[0][0] > best_ever[0]:
            best_ever = (paired[0][0], paired[0][3].copy(),
                         paired[0][1], paired[0][2])

        if gen % 10 == 0:
            elapsed = time.time() - t0
            top10_rel = np.mean([paired[i][2] for i in range(min(10, len(paired)))])
            # Count terminal usage in top 20
            lead_usage = Counter()
            for idx_p in range(min(20, len(paired))):
                estr = paired[idx_p][3].expr_str()
                for term in TERMINALS:
                    if term in estr:
                        lead_usage[term] += 1
            leads_str = ' '.join(f"{k}={v}" for k, v in lead_usage.most_common())
            print(f"    Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"rel={paired[0][2]:.3f}  top10_rel={top10_rel:.3f}  "
                  f"({elapsed:.0f}s)  leads=[{leads_str}]  "
                  f"{paired[0][3].expr_str()[:50]}", flush=True)

        # Elitism with hash-based diversity
        seen = set()
        new_pop, new_sc, new_sm, new_rl = [], [], [], []
        for s, sm, rl, t in paired[:n_elite * 3]:
            h = expr_hash(t)
            if h not in seen:
                new_pop.append(t.copy())
                new_sc.append(s)
                new_sm.append(sm)
                new_rl.append(rl)
                seen.add(h)
            if len(new_pop) >= n_elite:
                break
        while len(new_pop) < 2:
            new_pop.append(paired[0][3].copy())
            new_sc.append(paired[0][0])
            new_sm.append(paired[0][1])
            new_rl.append(paired[0][2])

        # Best-ever injection
        if best_ever[1] is not None:
            new_pop.append(best_ever[1].copy())
            new_sc.append(best_ever[0])
            new_sm.append(best_ever[2])
            new_rl.append(best_ever[3])

        # Immigration: half cross-lead, half random
        n_cross_imm = n_immigrant // 2
        for idx_imm in range(n_immigrant):
            if idx_imm < n_cross_imm:
                imm = random_cross_lead_tree(MAX_DEPTH)
            else:
                imm = simplify(random_tree(MAX_DEPTH))
            s, sm, rl = fitness_full(imm, train_beats, fs)
            new_pop.append(imm)
            new_sc.append(s)
            new_sm.append(sm)
            new_rl.append(rl)

        # Fill via tournament selection
        while len(new_pop) < POP_SIZE:
            def tournament():
                idxs = random.sample(range(len(paired)), min(TOURNAMENT_K, len(paired)))
                return paired[max(idxs, key=lambda i: paired[i][0])][3]

            r = random.random()
            if r < CROSSOVER_RATE:
                c1, c2 = crossover(tournament(), tournament())
                c1, c2 = simplify(c1), simplify(c2)
                s1, sm1, rl1 = fitness_full(c1, train_beats, fs)
                new_pop.append(c1); new_sc.append(s1)
                new_sm.append(sm1); new_rl.append(rl1)
                if len(new_pop) < POP_SIZE:
                    s2, sm2, rl2 = fitness_full(c2, train_beats, fs)
                    new_pop.append(c2); new_sc.append(s2)
                    new_sm.append(sm2); new_rl.append(rl2)
            elif r < CROSSOVER_RATE + MUTATION_RATE:
                child = simplify(mutate(tournament()))
                s, sm, rl = fitness_full(child, train_beats, fs)
                new_pop.append(child); new_sc.append(s)
                new_sm.append(sm); new_rl.append(rl)
            else:
                p = tournament()
                new_pop.append(p.copy())
                s, sm, rl = fitness_full(p, train_beats, fs)
                new_sc.append(s); new_sm.append(sm); new_rl.append(rl)

        population = new_pop[:POP_SIZE]
        scores = new_sc[:POP_SIZE]
        summaries = new_sm[:POP_SIZE]
        reliabilities = new_rl[:POP_SIZE]

        # Multi-stage dedup
        if gen in (15, 30, 45):
            paired = sorted(zip(scores, population), key=lambda x: -x[0])
            population = [t for _, t in paired]
            scores = [s for s, _ in paired]
            population, scores = deduplicate(population, scores, train_beats, fs)
            sms, rls = [], []
            for t in population:
                _, sm, rl = fitness_full(t, train_beats, fs)
                sms.append(sm); rls.append(rl)
            summaries = sms
            reliabilities = rls
            n_refill = POP_SIZE - len(population)
            for ri in range(n_refill):
                if ri < n_refill // 2:
                    imm = random_cross_lead_tree(MAX_DEPTH)
                else:
                    imm = simplify(random_tree(MAX_DEPTH))
                s, sm, rl = fitness_full(imm, train_beats, fs)
                population.append(imm); scores.append(s)
                summaries.append(sm); reliabilities.append(rl)

    # Final sort + dedup
    paired = sorted(zip(scores, summaries, reliabilities, population),
                    key=lambda x: -x[0])
    population = [t for _, _, _, t in paired]
    scores     = [s for s, _, _, _ in paired]
    population, scores = deduplicate(population, scores, train_beats, fs)
    final = []
    for i, tree in enumerate(population):
        sc, sm, rl = fitness_full(tree, train_beats, fs)
        final.append((sc, sm, rl, tree))
    final.sort(key=lambda x: -x[0])

    elapsed = time.time() - t0
    print(f"\n    GP complete: {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    if final:
        print(f"    Best: {final[0][0]:.4f} [{final[0][1]}] "
              f"-- {final[0][3].expr_str()[:70]}")
    print(f"    Unique features after dedup: {len(final)}")
    return final


# =============================================================================
# Baseline Features (expert-crafted ECG morphology)
# =============================================================================
def extract_baselines(beat):
    """Standard ECG morphological features for one beat window."""
    ecg1 = beat['ECG1']
    ecg2 = beat['ECG2']
    f = {}

    # Amplitude features (lead 1)
    f['R_amplitude'] = ecg1[BEAT_BEFORE]             # R-peak height
    f['ECG1_rms']    = np.sqrt(np.mean(ecg1 ** 2))
    f['ECG1_std']    = np.std(ecg1)
    f['ECG1_p2p']    = np.ptp(ecg1)                  # peak-to-peak
    f['ECG1_energy'] = np.sum(ecg1 ** 2)

    # Shape features (lead 1)
    f['ECG1_kurt'] = float(stats.kurtosis(ecg1, fisher=True))
    f['ECG1_skew'] = float(stats.skew(ecg1))

    # QRS region features (center ±20 samples ≈ ±56ms)
    qrs = ecg1[BEAT_BEFORE - 20: BEAT_BEFORE + 20]
    f['QRS_energy'] = np.sum(qrs ** 2)
    f['QRS_std']    = np.std(qrs)

    # Slope features
    deriv = np.gradient(ecg1, 1.0 / FS)
    f['max_slope']  = np.max(np.abs(deriv))
    f['slope_std']  = np.std(deriv)

    # T-wave region (~120 samples after R = 333ms post-R)
    t_idx = min(BEAT_BEFORE + 120, len(ecg1) - 1)
    f['T_wave_amp'] = ecg1[t_idx]

    # Lead 2 features
    f['ECG2_rms']  = np.sqrt(np.mean(ecg2 ** 2))
    f['ECG2_std']  = np.std(ecg2)
    f['ECG2_kurt'] = float(stats.kurtosis(ecg2, fisher=True))

    # Cross-lead features
    if np.std(ecg1) > 1e-10 and np.std(ecg2) > 1e-10:
        f['lead_corr'] = float(np.corrcoef(ecg1, ecg2)[0, 1])
    else:
        f['lead_corr'] = 0.0
    f['lead_ratio'] = f['ECG1_rms'] / (f['ECG2_rms'] + 1e-8)

    return f


# =============================================================================
# AUC Evaluation Utilities
# =============================================================================
def compute_auc_zscore(normal_vals, arrhythmia_vals):
    """AUC using |z-score| distance from normal distribution."""
    n_n = len(normal_vals)
    n_a = len(arrhythmia_vals)
    if n_n < 5 or n_a < 5:
        return 0.5
    mu  = np.mean(normal_vals)
    std = np.std(normal_vals)
    if std < 1e-10:
        return 0.5
    all_vals = np.concatenate([normal_vals, arrhythmia_vals])
    z = np.abs((all_vals - mu) / std)
    labels = np.array([0] * n_n + [1] * n_a)
    try:
        return float(roc_auc_score(labels, z))
    except:
        return 0.5


def gp_feature_values(tree, beats, fs, summary='std'):
    """Compute scalar feature value for each beat."""
    sfunc = SUMMARIES[summary]
    vals = []
    for beat in beats:
        signals = {k: v for k, v in beat.items() if isinstance(v, np.ndarray)}
        result = tree.evaluate(signals, fs)
        if result is None:
            vals.append(np.nan)
            continue
        finite = result[np.isfinite(result)]
        if len(finite) < 10:
            vals.append(np.nan)
        else:
            vals.append(sfunc(finite))
    return np.array(vals)


# =============================================================================
# MAIN
# =============================================================================
gp_results = run_gp(train_beats, FS)

TOP_N = min(30, len(gp_results))
gp_features = []
for rank, (score, smry, rel, tree) in enumerate(gp_results[:TOP_N]):
    gp_features.append({
        'rank': rank + 1,
        'score': score,
        'summary': smry,
        'reliability': rel,
        'expr': tree.expr_str(),
        'tree': tree,
    })

# =============================================================================
# [4] Per-Patient AUC Evaluation
# =============================================================================
print(f"\n[4] Per-patient AUC evaluation ({len(eval_patients)} patients, "
      f"{len(gp_features)} GP + baselines)...")
t0 = time.time()

# Gather baseline feature names from first patient
sample_beat = next(iter(eval_patients.values()))['normal'][0]
baseline_names = list(extract_baselines(sample_beat).keys())

# Storage: per-patient results
patient_results = []

MAX_EVAL_NORMAL = 200   # subsample normal beats for speed (keep all arrhythmia)

for pi, pid in enumerate(sorted(eval_patients.keys())):
    pdata = eval_patients[pid]
    all_normal = pdata['normal']
    arr_beats  = pdata['arrhythmia']
    n_normal = len(all_normal)
    n_arr    = len(arr_beats)

    # Subsample normal beats for evaluation speed
    if n_normal > MAX_EVAL_NORMAL:
        eval_normal = random.sample(all_normal, MAX_EVAL_NORMAL)
    else:
        eval_normal = all_normal

    # --- GP features (use training-selected summary only) ---
    gp_aucs = []
    for gf in gp_features:
        smry = gf['summary']
        norm_vals = gp_feature_values(gf['tree'], eval_normal, FS, smry)
        arr_vals  = gp_feature_values(gf['tree'], arr_beats, FS, smry)
        norm_vals = norm_vals[np.isfinite(norm_vals)]
        arr_vals  = arr_vals[np.isfinite(arr_vals)]
        gp_aucs.append(compute_auc_zscore(norm_vals, arr_vals))

    # --- Baseline features ---
    bl_aucs = {}
    norm_bl = [extract_baselines(b) for b in eval_normal]
    arr_bl  = [extract_baselines(b) for b in arr_beats]
    for bname in baseline_names:
        norm_vals = np.array([f[bname] for f in norm_bl])
        arr_vals  = np.array([f[bname] for f in arr_bl])
        norm_vals = norm_vals[np.isfinite(norm_vals)]
        arr_vals  = arr_vals[np.isfinite(arr_vals)]
        bl_aucs[bname] = compute_auc_zscore(norm_vals, arr_vals)

    if (pi + 1) % 5 == 0 or pi == 0:
        print(f"    Patient {pid} ({pi+1}/{len(eval_patients)}) done", flush=True)

    best_gp_auc = max(gp_aucs) if gp_aucs else 0.5
    best_gp_idx = gp_aucs.index(best_gp_auc) if gp_aucs else 0
    best_bl_name = max(bl_aucs, key=bl_aucs.get)
    best_bl_auc  = bl_aucs[best_bl_name]
    rank1_gp_auc = gp_aucs[0] if gp_aucs else 0.5

    patient_results.append({
        'pid': pid,
        'n_normal': n_normal,
        'n_arr': n_arr,
        'arr_types': Counter(pdata['arrhythmia_types']),
        'best_gp_auc': best_gp_auc,
        'best_gp_idx': best_gp_idx,
        'rank1_gp_auc': rank1_gp_auc,
        'best_bl_auc': best_bl_auc,
        'best_bl_name': best_bl_name,
        'gp_aucs': gp_aucs,
        'bl_aucs': bl_aucs,
    })

print(f"    Done in {time.time() - t0:.1f}s")

# =============================================================================
# [5] Results
# =============================================================================
print(f"\n[5] RESULTS")
print("=" * 90)

# -- Per-patient table --
print(f"\n  PER-PATIENT RESULTS (sorted by best GP AUC):")
print(f"  {'Rec':>5s}  {'N-beats':>7s}  {'A-beats':>7s}  {'Types':12s}"
      f"  {'GP-AUC':>7s}  {'BL-AUC':>7s}  {'Best-BL':15s}  {'Winner':>6s}")
print("  " + "-" * 85)

gp_wins, bl_wins, ties = 0, 0, 0
for pr in sorted(patient_results, key=lambda x: -x['best_gp_auc']):
    top_types = '+'.join(f"{s}{c}" for s, c in pr['arr_types'].most_common(3))
    if pr['best_gp_auc'] > pr['best_bl_auc'] + 0.01:
        winner = 'GP'
        gp_wins += 1
    elif pr['best_bl_auc'] > pr['best_gp_auc'] + 0.01:
        winner = 'BL'
        bl_wins += 1
    else:
        winner = 'TIE'
        ties += 1
    print(f"  {pr['pid']:>5s}  {pr['n_normal']:>7d}  {pr['n_arr']:>7d}  "
          f"{top_types:12s}  {pr['best_gp_auc']:>7.3f}  {pr['best_bl_auc']:>7.3f}  "
          f"{pr['best_bl_name']:15s}  {winner:>6s}")

# -- Aggregate statistics --
gp_median  = np.median([pr['best_gp_auc']  for pr in patient_results])
bl_median  = np.median([pr['best_bl_auc']  for pr in patient_results])
gp_mean    = np.mean([pr['best_gp_auc']    for pr in patient_results])
bl_mean    = np.mean([pr['best_bl_auc']    for pr in patient_results])
rank1_med  = np.median([pr['rank1_gp_auc'] for pr in patient_results])

print(f"\n  AGGREGATE ({len(patient_results)} patients):")
print(f"  {'Method':15s} {'Median AUC':>10s} {'Mean AUC':>10s}")
print("  " + "-" * 38)
print(f"  {'GP (best)':15s} {gp_median:>10.3f} {gp_mean:>10.3f}")
print(f"  {'GP (rank-1)':15s} {rank1_med:>10.3f}")
print(f"  {'Baseline':15s} {bl_median:>10.3f} {bl_mean:>10.3f}")

print(f"\n  HEAD-TO-HEAD (>1% AUC margin):")
print(f"    GP wins: {gp_wins}/{len(patient_results)} patients")
print(f"    BL wins: {bl_wins}/{len(patient_results)} patients")
print(f"    Ties:    {ties}/{len(patient_results)} patients")

# -- Which GP features work best across patients --
print(f"\n  TOP GP FEATURES (by median AUC across patients):")
gp_med_aucs = []
for gi, gf in enumerate(gp_features):
    aucs = [pr['gp_aucs'][gi] for pr in patient_results]
    gp_med_aucs.append((np.median(aucs), np.mean(aucs), gf))

gp_med_aucs.sort(key=lambda x: -x[0])
print(f"  {'#':>3s} {'Expression':45s} {'Smry':>5s} {'Fit':>5s} "
      f"{'Med':>6s} {'Mean':>6s}")
print("  " + "-" * 78)
for med, mean, gf in gp_med_aucs[:15]:
    expr = gf['expr'][:43]
    print(f"  {gf['rank']:3d} {expr:45s} {gf['summary']:>5s} {gf['score']:5.3f} "
          f"{med:>6.3f} {mean:>6.3f}")

# -- Which baselines work best --
print(f"\n  TOP BASELINE FEATURES (by median AUC across patients):")
bl_med_aucs = []
for bname in baseline_names:
    aucs = [pr['bl_aucs'][bname] for pr in patient_results]
    bl_med_aucs.append((np.median(aucs), np.mean(aucs), bname))

bl_med_aucs.sort(key=lambda x: -x[0])
print(f"  {'Feature':20s} {'Med':>6s} {'Mean':>6s}")
print("  " + "-" * 35)
for med, mean, bname in bl_med_aucs:
    print(f"  {bname:20s} {med:>6.3f} {mean:>6.3f}")

# -- Terminal usage --
print(f"\n  TERMINAL USAGE IN TOP-10 GP FEATURES:")
term_counts = Counter()
for _, _, gf in gp_med_aucs[:10]:
    for term in TERMINALS:
        if term in gf['expr']:
            term_counts[term] += 1
for sig, count in term_counts.most_common():
    print(f"    {sig:10s}: {count}/10")

# Cross-lead features
n_cross = 0
for _, _, gf in gp_med_aucs[:10]:
    if 'ECG1' in gf['expr'] and 'ECG2' in gf['expr']:
        n_cross += 1
print(f"    Cross-lead features: {n_cross}/10")

# =============================================================================
# Summary
# =============================================================================
print("\n" + "=" * 90)
print("  SUMMARY: MIT-BIH Arrhythmia Detection Experiment")
print("=" * 90)
print(f"  {len(eval_patients)} patients evaluated, "
      f"{total_normal:,} normal + {total_arrhythmia:,} arrhythmia beats")
print(f"  GP trained on {N_TRAIN} pooled normal beats, "
      f"evaluated per-patient via |z-score| AUC")
if gp_med_aucs:
    best_gp = gp_med_aucs[0]
    print(f"  Best GP feature: median AUC {best_gp[0]:.3f}  "
          f"-- {best_gp[2]['expr'][:60]}")
if bl_med_aucs:
    best_bl = bl_med_aucs[0]
    print(f"  Best baseline:   median AUC {best_bl[0]:.3f}  "
          f"-- {best_bl[2]}")
print(f"  GP median AUC:      {gp_median:.3f}")
print(f"  Baseline median AUC: {bl_median:.3f}")
if gp_median > bl_median + 0.01:
    print(f"  >>> GP WINS by {gp_median - bl_median:.3f} AUC")
elif bl_median > gp_median + 0.01:
    print(f"  >>> BASELINE WINS by {bl_median - gp_median:.3f} AUC")
else:
    print(f"  >>> NEAR TIE (within 0.01 AUC)")
print(f"  Win/Loss/Tie: GP {gp_wins} / BL {bl_wins} / Tie {ties}")
print("=" * 90)
print("  Done.")
