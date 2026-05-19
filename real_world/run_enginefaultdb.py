"""
EngineFaultDB GP Experiment — Real Engine Data
===============================================
C14NE spark ignition engine, controlled lab conditions.
14 variables, 55999 samples, 4 classes (normal + 3 fault types).

Design:
  - Chunk data into windows of WINDOW_SIZE consecutive samples = "trials"
  - GP trains on normal (fault=0) trials only
  - Evaluate AUC against fault types 1, 2, 3
  - Compare GP-discovered features vs hand-crafted baselines

Type system (engine physics):
  Pr = Pressure (MAP)
  R  = Rotation (RPM)
  M  = Mechanical (Force, Power, Speed)
  C  = Combustion (TPS, Consumption)
  E  = Emission (CO, HC, CO2, O2)
  Ra = Ratio (Lambda, AFR)
  S  = Scalar (derived)
"""
import numpy as np
from scipy import stats
from copy import deepcopy
import random, sys, os, time, warnings
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

np.random.seed(42)
random.seed(42)

# ── Parameters ─────────────────────────────────────────────────────��───────
WINDOW_SIZE = 200     # samples per trial
FS = 1.0              # treat as 1 Hz (no real time info)
N_TRAIN_TRIALS = 60   # healthy trials for GP training
N_TEST_TRIALS = 30    # healthy + fault trials for evaluation

POP_SIZE = 200
GENERATIONS = 80
MAX_DEPTH = 4
TOURNAMENT_K = 5
ELITE_FRAC = 0.05
CROSSOVER_RATE = 0.60
MUTATION_RATE = 0.30
IMMIGRATION_RATE = 0.10
RSTD_WINDOW = 20      # rolling window within a trial

print("=" * 70)
print("  EngineFaultDB GP Experiment — Real Engine Data (Hard Mode)")
print("=" * 70)

# ── Load Data ───────────────────────────��──────────────────────────────────
DATA_PATH = os.path.join(os.path.dirname(__file__), 'EngineFaultDB', 'EngineFaultDB_Final.csv')
print(f"\n[1] Loading {DATA_PATH}...")
df = pd.read_csv(DATA_PATH)
print(f"    Shape: {df.shape}, Fault classes: {sorted(df['Fault'].unique())}")
print(f"    Class distribution: {dict(df['Fault'].value_counts().sort_index())}")

# Signal columns (everything except Fault label)
SIGNAL_COLS = [c for c in df.columns if c != 'Fault']
print(f"    Signals: {SIGNAL_COLS}")

# Normalize signals to zero mean, unit variance (on healthy data only)
healthy_df = df[df['Fault'] == 0].copy()
means = healthy_df[SIGNAL_COLS].mean()
stds = healthy_df[SIGNAL_COLS].std()
stds[stds < 1e-10] = 1.0  # avoid div by zero

for col in SIGNAL_COLS:
    df[col] = (df[col] - means[col]) / stds[col]

# ── Create trial windows ──────────────────────────────���────────────────────
def make_trials(subset_df, n_trials, window_size=WINDOW_SIZE):
    """Split dataframe into trial windows of consecutive samples."""
    trials = []
    indices = subset_df.index.tolist()
    # Shuffle to get diverse windows
    np.random.shuffle(indices)

    for i in range(0, len(indices) - window_size, window_size):
        chunk_idx = indices[i:i+window_size]
        chunk = subset_df.loc[chunk_idx]
        trial = {col: chunk[col].values for col in SIGNAL_COLS}
        trials.append(trial)
        if len(trials) >= n_trials:
            break
    return trials

print("\n[2] Creating trial windows...")
# Healthy trials
healthy_all = make_trials(df[df['Fault'] == 0], N_TRAIN_TRIALS + N_TEST_TRIALS)
healthy_train = healthy_all[:N_TRAIN_TRIALS]
healthy_test = healthy_all[N_TRAIN_TRIALS:N_TRAIN_TRIALS + N_TEST_TRIALS]

# Fault trials
fault1_trials = make_trials(df[df['Fault'] == 1], N_TEST_TRIALS)
fault2_trials = make_trials(df[df['Fault'] == 2], N_TEST_TRIALS)
fault3_trials = make_trials(df[df['Fault'] == 3], N_TEST_TRIALS)

print(f"    Healthy train: {len(healthy_train)}, Healthy test: {len(healthy_test)}")
print(f"    Fault 1: {len(fault1_trials)}, Fault 2: {len(fault2_trials)}, Fault 3: {len(fault3_trials)}")

# ── Type System ────────────────────────────────────────────────────────────
TYPES = ('Pr', 'R', 'M', 'C', 'E', 'Ra', 'S')

TERMINALS = {
    'MAP':     'Pr',   # Manifold Absolute Pressure
    'RPM':     'R',    # Engine speed
    'TPS':     'C',    # Throttle Position
    'Force':   'M',    # Torque/Force
    'Power':   'M',    # Power output
    'Speed':   'M',    # Vehicle speed
    'ConsLH':  'C',    # Fuel consumption L/H
    'CO':      'E',    # Carbon monoxide
    'HC':      'E',    # Hydrocarbons
    'CO2':     'E',    # Carbon dioxide
    'O2':      'E',    # Oxygen
    'Lambda':  'Ra',   # Air-fuel equivalence
    'AFR':     'Ra',   # Air-fuel ratio
}

# Map CSV column names to terminal names
COL_TO_TERM = {
    'MAP': 'MAP', 'TPS': 'TPS', 'Force': 'Force', 'Power': 'Power',
    'RPM': 'RPM', 'Consumption L/H': 'ConsLH', 'Consumption L/100KM': 'ConsLH',
    'Speed': 'Speed', 'CO': 'CO', 'HC': 'HC', 'CO2': 'CO2', 'O2': 'O2',
    'Lambda': 'Lambda', 'AFR': 'AFR'
}
# Rebuild trials with terminal names
def rename_trial(trial):
    renamed = {}
    for col, vals in trial.items():
        term = COL_TO_TERM.get(col)
        if term and term not in renamed:
            renamed[term] = vals
    return renamed

healthy_train = [rename_trial(t) for t in healthy_train]
healthy_test = [rename_trial(t) for t in healthy_test]
fault1_trials = [rename_trial(t) for t in fault1_trials]
fault2_trials = [rename_trial(t) for t in fault2_trials]
fault3_trials = [rename_trial(t) for t in fault3_trials]

FIRST_SIGNAL = 'MAP'
CONSTS = [0.5, 1.0, 2.0, 5.0]

# ── Operators ────────────────────────��─────────────────────────────────────
OPS = []

# Temporal derivative
for t in TYPES:
    if t != 'S':
        OPS.append(('ddt', 1, (t,), t, lambda a, fs: np.gradient(a, 1/fs)))

# Rolling std
def _rstd(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w: return np.zeros(n)
    cs = np.concatenate([[0], np.cumsum(a)])
    cs2 = np.concatenate([[0], np.cumsum(a**2)])
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = np.maximum(0, s2/w - (s/w)**2)
    result = np.zeros(n)
    result[w-1:] = np.sqrt(var)
    return result

for t in TYPES:
    if t != 'S':
        OPS.append(('rstd', 1, (t,), t, lambda a, fs: _rstd(a, fs)))

# Absolute value
for t in TYPES:
    OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))

# Negation
for t in TYPES:
    OPS.append(('neg', 1, (t,), t, lambda a, fs: -a))

# Same-type add/sub
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Cross-type ratios (physically meaningful)
OPS.append(('div', 2, ('M', 'M'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # Power/Force, Speed/Speed
OPS.append(('div', 2, ('M', 'R'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # Power/RPM, Force/RPM
OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # CO/CO2, HC/O2
OPS.append(('div', 2, ('C', 'R'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # Consumption/RPM
OPS.append(('div', 2, ('C', 'M'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # Consumption/Power = SFC
OPS.append(('div', 2, ('Pr', 'R'), 'S', lambda a, b, fs: a / (b + 1e-8)))  # MAP/RPM
OPS.append(('div', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a / (b + 1e-8))) # Lambda/AFR
OPS.append(('div', 2, ('M', 'C'), 'S', lambda a, b, fs: a / (b + 1e-8)))   # Power/Consumption = efficiency

# Cross-type products
OPS.append(('mul', 2, ('Pr', 'R'), 'M', lambda a, b, fs: a * b))   # MAP*RPM ~ airflow
OPS.append(('mul', 2, ('C', 'R'), 'M', lambda a, b, fs: a * b))    # TPS*RPM ~ load
OPS.append(('mul', 2, ('S', 'M'), 'M', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'C'), 'C', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))

# Cross-type subtraction → Scalar (for comparing related signals)
OPS.append(('sub_x', 2, ('E', 'E'), 'S', lambda a, b, fs: a - b))     # CO-CO2, O2-CO etc
OPS.append(('sub_x', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a - b))   # Lambda-AFR offset
OPS.append(('sub_x', 2, ('M', 'M'), 'S', lambda a, b, fs: a - b))     # Power-Force, Speed diffs

# Build lookup
UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1]==1 and o[2]==(t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1]==2 and o[2]==key]

print(f"    Types: {len(TYPES)}, Terminals: {len(TERMINALS)}, Operators: {len(OPS)}")

# ── Expression Tree ────────────────────────────────────────────────────────
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

    def evaluate(self, signals, fs, _depth=0):
        if _depth > 10: return None
        try:
            if self.kind == 'terminal': return signals[self.name].copy()
            elif self.kind == 'const':
                return np.full(len(signals[FIRST_SIGNAL]), self.const_val)
            elif self.kind == 'unary':
                child_val = self.children[0].evaluate(signals, fs, _depth+1)
                if child_val is None: return None
                return self.func(child_val, fs)
            elif self.kind == 'binary':
                left = self.children[0].evaluate(signals, fs, _depth+1)
                right = self.children[1].evaluate(signals, fs, _depth+1)
                if left is None or right is None: return None
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except: return None

    def expr_str(self):
        if self.kind == 'terminal': return self.name
        elif self.kind == 'const': return f"{self.const_val:.1f}"
        elif self.kind == 'unary': return f"{self.name}({self.children[0].expr_str()})"
        elif self.kind == 'binary':
            return f"({self.children[0].expr_str()} {self.name} {self.children[1].expr_str()})"

    def copy(self):
        new = Node(self.kind, self.name, self.out_type, self.func, None, self.const_val)
        new.children = [c.copy() for c in self.children] if self.children else []
        return new


# ── Tree Generation ─────────────────────────��──────────────────────────────
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


# ── Tree Manipulation ─────────────────────────��────────────────────────────
def _all_nodes(node, path, parent, idx):
    yield node, path, parent, idx
    for i, child in enumerate(node.children):
        yield from _all_nodes(child, path + [i], node, i)

def mutate(tree, max_depth=MAX_DEPTH):
    tree = tree.copy()
    nodes = list(_all_nodes(tree, [], None, None))
    if not nodes: return tree
    node, path, parent, idx = random.choice(nodes)
    new_sub = random_tree(max(1, max_depth - len(path)), node.out_type)
    if parent is None: return new_sub
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
                n1_copy = n1.copy()
                n2_copy = n2.copy()
                par1.children[idx1] = n2_copy
                par2.children[idx2] = n1_copy
                return t1, t2
    return t1, t2


# ── Fitness (v5) ───────────────────────────────────────────────────────────
def evaluate_feature(tree, trials, fs):
    """Return one scalar (std) per trial."""
    scalars = []
    for trial in trials:
        result = tree.evaluate(trial, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 20:
            return None
        scalars.append(np.std(finite))
    return np.array(scalars)

def split_half_reliability(tree, trials, fs):
    first_half, second_half = [], []
    for trial in trials:
        result = tree.evaluate(trial, fs)
        if result is None: return 0.0
        finite = result[np.isfinite(result)]
        if len(finite) < 40: return 0.0
        mid = len(finite) // 2
        first_half.append(np.std(finite[:mid]))
        second_half.append(np.std(finite[mid:]))
    if len(first_half) < 10 or np.std(first_half) < 1e-10 or np.std(second_half) < 1e-10:
        return 0.0
    r = np.corrcoef(first_half, second_half)[0, 1]
    return max(0.0, r) if np.isfinite(r) else 0.0

def composite_fitness(tree, trials, fs):
    vals = evaluate_feature(tree, trials, fs)
    if vals is None or len(vals) < 15:
        return 0.0, {}
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0, {}

    # Tightness
    c, _ = np.histogram(vals, bins=min(20, len(vals)//3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)

    # Stationarity
    chunks = np.array_split(vals, 5)
    chunk_means = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(chunk_means) < 2 or abs(np.mean(chunk_means)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(chunk_means) / (abs(np.mean(chunk_means)) + 1e-8))

    # Headroom
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))

    # Reliability
    reliability = split_half_reliability(tree, trials, fs)

    # Penalties
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

    if reliability < 0.3:
        score = penalty * (tightness + stationarity + headroom + reliability) / 4.0 * 0.7
    else:
        score = penalty * (tightness + stationarity + headroom + reliability) / 4.0

    return score, {'tightness': tightness, 'stationarity': stationarity,
                   'headroom': headroom, 'reliability': reliability, 'composite': score}


# ── GP Evolution ─────────────────────────────────���─────────────────────────
def expr_hash(tree):
    s = tree.expr_str()
    return hash(s[:60])

def deduplicate(population, scores, trials, fs, threshold=0.95):
    n_check = min(30, len(population))
    vectors = []
    for i in range(n_check):
        v = evaluate_feature(population[i], trials, fs)
        vectors.append(v if v is not None else np.zeros(len(trials)))

    keep = [True] * n_check
    for i in range(1, n_check):
        if not keep[i]: continue
        for j in range(i):
            if not keep[j]: continue
            if len(vectors[i]) == len(vectors[j]) and np.std(vectors[i]) > 1e-10 and np.std(vectors[j]) > 1e-10:
                r = abs(np.corrcoef(vectors[i], vectors[j])[0, 1])
                if np.isfinite(r) and r > threshold:
                    keep[i] = False
                    break

    new_pop = [population[i] for i in range(n_check) if keep[i]]
    new_scores = [scores[i] for i in range(n_check) if keep[i]]
    new_pop.extend(population[n_check:])
    new_scores.extend(scores[n_check:])
    return new_pop, new_scores


def run_gp(trials, fs):
    print(f"\n[3] Running GP: pop={POP_SIZE}, gen={GENERATIONS}, "
          f"depth<={MAX_DEPTH}, {len(trials)} trials")

    population = [random_tree(MAX_DEPTH) for _ in range(POP_SIZE)]
    scores = [composite_fitness(t, trials, fs)[0] for t in population]

    n_elite = max(2, int(POP_SIZE * ELITE_FRAC))
    n_immigrant = max(2, int(POP_SIZE * IMMIGRATION_RATE))
    best_ever = (0.0, None)

    t0 = time.time()
    for gen in range(GENERATIONS):
        paired = sorted(zip(scores, population), key=lambda x: -x[0])

        if paired[0][0] > best_ever[0]:
            best_ever = (paired[0][0], paired[0][1].copy())

        if gen % 10 == 0:
            elapsed = time.time() - t0
            expr = paired[0][1].expr_str()[:65]
            print(f"    Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"median={paired[POP_SIZE//2][0]:.4f}  "
                  f"({elapsed:.0f}s)  {expr}")

        # Elitism with diversity
        seen = set()
        new_pop, new_scores = [], []
        for s, t in paired[:n_elite * 3]:
            h = expr_hash(t)
            if h not in seen:
                new_pop.append(t.copy())
                new_scores.append(s)
                seen.add(h)
            if len(new_pop) >= n_elite:
                break
        while len(new_pop) < 2:
            new_pop.append(paired[0][1].copy())
            new_scores.append(paired[0][0])

        # Immigration
        for _ in range(n_immigrant):
            imm = random_tree(MAX_DEPTH)
            s, _ = composite_fitness(imm, trials, fs)
            new_pop.append(imm)
            new_scores.append(s)

        # Fill via tournament
        while len(new_pop) < POP_SIZE:
            def tournament():
                idxs = random.sample(range(len(paired)), min(TOURNAMENT_K, len(paired)))
                return paired[max(idxs, key=lambda i: paired[i][0])][1]

            r = random.random()
            if r < CROSSOVER_RATE:
                c1, c2 = crossover(tournament(), tournament())
                s1, _ = composite_fitness(c1, trials, fs)
                new_pop.append(c1); new_scores.append(s1)
                if len(new_pop) < POP_SIZE:
                    s2, _ = composite_fitness(c2, trials, fs)
                    new_pop.append(c2); new_scores.append(s2)
            elif r < CROSSOVER_RATE + MUTATION_RATE:
                child = mutate(tournament())
                s, _ = composite_fitness(child, trials, fs)
                new_pop.append(child); new_scores.append(s)
            else:
                p = tournament()
                new_pop.append(p.copy())
                s, _ = composite_fitness(p, trials, fs)
                new_scores.append(s)

        population = new_pop[:POP_SIZE]
        scores = new_scores[:POP_SIZE]

        # Dedup at gen 40
        if gen == 40:
            paired = sorted(zip(scores, population), key=lambda x: -x[0])
            population = [t for _, t in paired]
            scores = [s for s, _ in paired]
            population, scores = deduplicate(population, scores, trials, fs)
            while len(population) < POP_SIZE:
                imm = random_tree(MAX_DEPTH)
                s, _ = composite_fitness(imm, trials, fs)
                population.append(imm); scores.append(s)

    # Final sort + dedup
    paired = sorted(zip(scores, population), key=lambda x: -x[0])
    population = [t for _, t in paired]
    scores = [s for s, _ in paired]
    population, scores = deduplicate(population, scores, trials, fs)

    elapsed = time.time() - t0
    print(f"\n    GP complete: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"    Best: {scores[0]:.4f} -- {population[0].expr_str()[:80]}")
    print(f"    Unique features after dedup: {len(population)}")
    return list(zip(scores, population))


# ── Baselines ──────────────────────────────────────────────────────────────
BASELINE_FEATURES = {
    'MAP_std':      lambda t: np.std(t['MAP']),
    'RPM_std':      lambda t: np.std(t['RPM']),
    'TPS_std':      lambda t: np.std(t['TPS']),
    'Force_std':    lambda t: np.std(t['Force']),
    'Power_std':    lambda t: np.std(t['Power']),
    'Speed_std':    lambda t: np.std(t['Speed']),
    'CO_std':       lambda t: np.std(t['CO']),
    'HC_std':       lambda t: np.std(t['HC']),
    'CO2_std':      lambda t: np.std(t['CO2']),
    'O2_std':       lambda t: np.std(t['O2']),
    'Lambda_std':   lambda t: np.std(t['Lambda']),
    'AFR_std':      lambda t: np.std(t['AFR']),
    'MAP_mean':     lambda t: np.mean(t['MAP']),
    'RPM_mean':     lambda t: np.mean(t['RPM']),
    'Power_mean':   lambda t: np.mean(t['Power']),
    'CO_mean':      lambda t: np.mean(t['CO']),
    'HC_mean':      lambda t: np.mean(t['HC']),
    'Lambda_mean':  lambda t: np.mean(t['Lambda']),
    'SFC':          lambda t: np.mean(t['ConsLH'] / (t['Power'] + 1e-8)),  # specific fuel consumption
    'VE_proxy':     lambda t: np.mean(t['MAP'] * t['RPM']),  # volumetric efficiency proxy
    'comb_eff':     lambda t: np.mean(t['CO2'] / (t['CO'] + t['CO2'] + 1e-8)),  # combustion efficiency
    'power_rpm':    lambda t: np.mean(t['Power'] / (t['RPM'] + 1e-8)),  # torque proxy
    'HC_CO_ratio':  lambda t: np.mean(t['HC'] / (t['CO'] + 1e-8)),
    'O2_CO2_ratio': lambda t: np.mean(t['O2'] / (t['CO2'] + 1e-8)),
}


# ── AUC ─────────────────────────────��──────────────────────────────────────
def compute_auc(healthy_vals, fault_vals):
    mu, sigma = np.mean(healthy_vals), np.std(healthy_vals)
    if sigma < 1e-10: return 0.5
    h_z = np.abs((healthy_vals - mu) / sigma)
    f_z = np.abs((fault_vals - mu) / sigma)
    labels = np.concatenate([np.zeros(len(h_z)), np.ones(len(f_z))])
    scores_arr = np.concatenate([h_z, f_z])
    order = np.argsort(-scores_arr)
    labels_sorted = labels[order]
    n_pos = labels_sorted.sum()
    n_neg = len(labels_sorted) - n_pos
    if n_pos == 0 or n_neg == 0: return 0.5
    tp = 0; auc = 0.0
    for lab in labels_sorted:
        if lab == 1: tp += 1
        else: auc += tp
    return auc / (n_pos * n_neg)


# ── Main ───────────────────────────────────────────────────────────────────
gp_results = run_gp(healthy_train, FS)

print("\n[4] Evaluating GP features...")
TOP_N = 50
gp_features = []
for score, tree in gp_results[:TOP_N]:
    _, comps = composite_fitness(tree, healthy_train, FS)
    h_vals = evaluate_feature(tree, healthy_test, FS)
    if h_vals is None: continue

    aucs = {}
    for fname, ftrials in [('fault1', fault1_trials), ('fault2', fault2_trials), ('fault3', fault3_trials)]:
        f_vals = evaluate_feature(tree, ftrials, FS)
        aucs[fname] = compute_auc(h_vals, f_vals) if f_vals is not None else 0.5

    rel = split_half_reliability(tree, healthy_train, FS)
    gp_features.append({
        'expr': tree.expr_str(), 'score': score, 'reliability': rel,
        'components': comps, 'aucs': aucs,
        'avg_auc': np.mean(list(aucs.values())),
    })

print("[5] Evaluating baselines...")
baseline_results = []
for name, func in BASELINE_FEATURES.items():
    h_vals = np.array([func(t) for t in healthy_test])
    aucs = {}
    for fname, ftrials in [('fault1', fault1_trials), ('fault2', fault2_trials), ('fault3', fault3_trials)]:
        f_vals = np.array([func(t) for t in ftrials])
        aucs[fname] = compute_auc(h_vals, f_vals)
    baseline_results.append({'name': name, 'aucs': aucs, 'avg_auc': np.mean(list(aucs.values()))})

# ── Print Results ──────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  RESULTS: GP vs BASELINES on EngineFaultDB (Real Engine Data)")
print("=" * 90)

print("\n  BASELINES (sorted by avg AUC):")
print(f"  {'Feature':20s} {'Fault1':>8s} {'Fault2':>8s} {'Fault3':>8s} {'Avg':>8s}")
print("  " + "-" * 55)
for b in sorted(baseline_results, key=lambda x: -x['avg_auc'])[:15]:
    print(f"  {b['name']:20s} {b['aucs']['fault1']:8.3f} {b['aucs']['fault2']:8.3f} "
          f"{b['aucs']['fault3']:8.3f} {b['avg_auc']:8.3f}")

print(f"\n  TOP GP FEATURES (sorted by avg AUC):")
print(f"  {'#':>3s} {'Expression':45s} {'Score':>6s} {'R':>5s} "
      f"{'F1':>6s} {'F2':>6s} {'F3':>6s} {'Avg':>6s}")
print("  " + "-" * 90)
for i, gf in enumerate(sorted(gp_features, key=lambda x: -x['avg_auc'])[:20]):
    expr = gf['expr'][:43]
    print(f"  {i+1:3d} {expr:45s} {gf['score']:6.3f} {gf['reliability']:5.2f} "
          f"{gf['aucs']['fault1']:6.3f} {gf['aucs']['fault2']:6.3f} "
          f"{gf['aucs']['fault3']:6.3f} {gf['avg_auc']:6.3f}")

# Head-to-head
print(f"\n  HEAD-TO-HEAD (best per fault):")
print(f"  {'Fault':8s} {'GP AUC':>8s} {'GP Feature':40s} {'Base AUC':>9s} {'Baseline':20s} {'Winner':>8s}")
print("  " + "-" * 100)
for fname in ['fault1', 'fault2', 'fault3']:
    best_gp = max(gp_features, key=lambda x: x['aucs'][fname]) if gp_features else None
    best_bl = max(baseline_results, key=lambda x: x['aucs'][fname])
    gp_auc = best_gp['aucs'][fname] if best_gp else 0.5
    bl_auc = best_bl['aucs'][fname]
    winner = 'GP' if gp_auc > bl_auc + 0.01 else ('BASE' if bl_auc > gp_auc + 0.01 else 'TIE')
    expr = best_gp['expr'][:38] if best_gp else 'N/A'
    print(f"  {fname:8s} {gp_auc:8.3f} {expr:40s} {bl_auc:9.3f} {best_bl['name']:20s} {winner:>8s}")

# Population quality
n_good = sum(1 for gf in gp_features if gf['avg_auc'] > 0.70)
n_reliable = sum(1 for gf in gp_features if gf['reliability'] > 0.5)
top10_rel = np.mean([gf['reliability'] for gf in sorted(gp_features, key=lambda x: -x['score'])[:10]]) if gp_features else 0
top10_auc = np.mean([gf['avg_auc'] for gf in sorted(gp_features, key=lambda x: -x['avg_auc'])[:10]]) if gp_features else 0

print(f"\n  POPULATION QUALITY:")
print(f"    GP features evaluated: {len(gp_features)}")
print(f"    Features with avg AUC > 0.70: {n_good}/{len(gp_features)} ({100*n_good/max(1,len(gp_features)):.0f}%)")
print(f"    Features with reliability > 0.5: {n_reliable}/{len(gp_features)}")
print(f"    Top-10 avg reliability: {top10_rel:.3f}")
print(f"    Top-10 avg AUC: {top10_auc:.3f}")

# What signals does GP use?
print(f"\n  SIGNAL USAGE IN TOP-10 GP FEATURES:")
signal_counts = {}
for gf in sorted(gp_features, key=lambda x: -x['avg_auc'])[:10]:
    for term in TERMINALS:
        if term in gf['expr']:
            signal_counts[term] = signal_counts.get(term, 0) + 1
for sig, count in sorted(signal_counts.items(), key=lambda x: -x[1]):
    print(f"    {sig:10s}: {count}/10")

print("\n" + "=" * 70)
print("  Experiment complete.")
print("=" * 70)
