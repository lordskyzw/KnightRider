"""
C-MAPSS FD001 GP Experiment -- NASA Turbofan Run-to-Failure
==========================================================
100 turbofan engines run to failure under sea-level conditions.
HPC (High Pressure Compressor) degradation fault.
21 sensors, 3 operational settings (constant for FD001).

Key question: Can GP features trained on early healthy cycles
detect degradation EARLIER than standard hand-crafted baselines?

Design:
  - Each engine: sequence of 130-350 cycles with 14 useful sensors
  - Window W consecutive cycles -> one GP "trial"
  - Train GP on early healthy windows (first 30% of each engine)
  - Track features across full engine life (all 100 engines)
  - Detection = z-score > threshold for consecutive windows
  - Compare: GP vs baselines on early detection across fleet

Type system (turbofan physics):
  T    = Temperature (T24, T30, T50)
  P    = Pressure (P30, Ps30)
  N    = Speed (Nf, Nc, NRf, NRc)
  Ra   = Ratio (BPR, phi)
  F    = Flow (W31, W32)
  Ctrl = Control (htBleed)
  S    = Scalar (derived)
"""
import numpy as np
from scipy import stats
from collections import Counter
import random, sys, os, time, warnings

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

np.random.seed(42)
random.seed(42)

# -- Parameters ---------------------------------------------------------------
WINDOW = 30           # cycles per trial window
HEALTHY_FRAC = 0.30   # first 30% of each engine's life = healthy
FS = 1.0              # 1 cycle per step
N_TRAIN_WINDOWS = 60  # healthy windows for GP training
RSTD_WINDOW = 8       # rolling window within a trial

# Detection thresholds
THRESHOLD = 3.0       # z-score sigma
MIN_CONSEC = 5        # consecutive windows above threshold

# GP parameters
POP_SIZE = 200
GENERATIONS = 80
MAX_DEPTH = 4
TOURNAMENT_K = 5
ELITE_FRAC = 0.05
CROSSOVER_RATE = 0.60
MUTATION_RATE = 0.30
IMMIGRATION_RATE = 0.15

print("=" * 80)
print("  NASA C-MAPSS FD001 GP Experiment -- Turbofan Run-to-Failure")
print("  Train on early healthy cycles | Track degradation to failure")
print("  100 engines, HPC degradation, Sea Level conditions")
print("=" * 80)

# -- [1] Load Data ------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), 'cmapss_data', 'CMaps')

SENSOR_NAMES = [
    'T2', 'T24', 'T30', 'T50', 'P2', 'P15', 'P30',
    'Nf', 'Nc', 'epr', 'Ps30', 'phi', 'NRf', 'NRc',
    'BPR', 'farB', 'htBleed', 'Nf_dmd', 'PCNfR_dmd', 'W31', 'W32'
]

CONST_SENSORS = {'T2', 'P2', 'P15', 'epr', 'farB', 'Nf_dmd', 'PCNfR_dmd'}
USEFUL_SENSORS = [s for s in SENSOR_NAMES if s not in CONST_SENSORS]

print(f"\n[1] Loading C-MAPSS FD001 data...")
t_start = time.time()
raw = np.loadtxt(os.path.join(DATA_DIR, 'train_FD001.txt'))
print(f"    Raw shape: {raw.shape}  ({time.time()-t_start:.1f}s)")

# Parse into per-engine data
engines = {}
unit_ids = sorted(set(raw[:, 0].astype(int)))
for uid in unit_ids:
    mask = raw[:, 0] == uid
    engine_data = raw[mask]
    sensors = {}
    for i, sname in enumerate(SENSOR_NAMES):
        sensors[sname] = engine_data[:, 5 + i]
    engines[uid] = {
        'sensors': {k: v for k, v in sensors.items() if k in USEFUL_SENSORS},
        'n_cycles': len(engine_data),
    }

life_lengths = [e['n_cycles'] for e in engines.values()]
print(f"    {len(engines)} engines loaded")
print(f"    Life lengths: min={min(life_lengths)}, max={max(life_lengths)}, "
      f"mean={np.mean(life_lengths):.0f}, median={np.median(life_lengths):.0f}")

# -- [2] Normalize on healthy data --------------------------------------------
print(f"\n[2] Normalizing on healthy data (first {HEALTHY_FRAC*100:.0f}% of each engine)...")
all_healthy = {s: [] for s in USEFUL_SENSORS}
for uid, eng in engines.items():
    n_h = max(WINDOW, int(eng['n_cycles'] * HEALTHY_FRAC))
    for s in USEFUL_SENSORS:
        all_healthy[s].extend(eng['sensors'][s][:n_h])

sensor_mu = {s: np.mean(all_healthy[s]) for s in USEFUL_SENSORS}
sensor_std = {s: max(np.std(all_healthy[s]), 1e-10) for s in USEFUL_SENSORS}

for uid in engines:
    for s in USEFUL_SENSORS:
        engines[uid]['sensors'][s] = (
            (engines[uid]['sensors'][s] - sensor_mu[s]) / sensor_std[s]
        )

print(f"    {len(USEFUL_SENSORS)} useful sensors: {USEFUL_SENSORS}")

# -- Create training windows --------------------------------------------------
print(f"\n[3] Creating training windows (window={WINDOW} cycles)...")
healthy_windows = []
for uid, eng in engines.items():
    n_h = max(WINDOW, int(eng['n_cycles'] * HEALTHY_FRAC))
    for start in range(0, n_h - WINDOW + 1, WINDOW // 2):
        trial = {s: eng['sensors'][s][start:start + WINDOW] for s in USEFUL_SENSORS}
        healthy_windows.append(trial)

print(f"    Total healthy windows available: {len(healthy_windows)}")
random.shuffle(healthy_windows)
train_windows = healthy_windows[:N_TRAIN_WINDOWS]
print(f"    Training windows selected: {len(train_windows)}")

# -- Type System --------------------------------------------------------------
TYPES = ('T', 'P', 'N', 'Ra', 'F', 'Ctrl', 'S')

TERMINALS = {
    'T24':     'T',     # LPC outlet temperature
    'T30':     'T',     # HPC outlet temperature
    'T50':     'T',     # LPT outlet temperature
    'P30':     'P',     # HPC outlet pressure
    'Ps30':    'P',     # HPC static pressure
    'Nf':      'N',     # Fan speed
    'Nc':      'N',     # Core speed
    'NRf':     'N',     # Corrected fan speed
    'NRc':     'N',     # Corrected core speed
    'BPR':     'Ra',    # Bypass ratio
    'phi':     'Ra',    # Fuel flow / Ps30
    'W31':     'F',     # HPT coolant bleed
    'W32':     'F',     # LPT coolant bleed
    'htBleed': 'Ctrl',  # Bleed enthalpy
}

FIRST_SIGNAL = 'T24'
CONSTS = [0.5, 1.0, 2.0, 5.0]

# -- Operators ----------------------------------------------------------------
OPS = []

# Temporal derivative (degradation rate)
for t in TYPES:
    if t != 'S':
        OPS.append(('ddt', 1, (t,), t, lambda a, fs: np.gradient(a, 1/fs)))


def _rstd(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w:
        return np.zeros(n)
    cs = np.concatenate([[0], np.cumsum(a)])
    cs2 = np.concatenate([[0], np.cumsum(a**2)])
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = np.maximum(0, s2 / w - (s / w) ** 2)
    result = np.zeros(n)
    result[w - 1:] = np.sqrt(var)
    return result


def _rmean(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w:
        return a.copy()
    cs = np.concatenate([[0], np.cumsum(a)])
    s = cs[w:] - cs[:-w]
    result = np.zeros(n)
    result[w - 1:] = s / w
    return result


for t in TYPES:
    if t != 'S':
        OPS.append(('rstd', 1, (t,), t, lambda a, fs: _rstd(a, fs)))
        OPS.append(('rmean', 1, (t,), t, lambda a, fs: _rmean(a, fs)))

for t in TYPES:
    OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))

for t in TYPES:
    OPS.append(('neg', 1, (t,), t, lambda a, fs: -a))

# Same-type add/sub
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Cross-type ratios (thermodynamically meaningful)
OPS.append(('div', 2, ('T', 'T'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # temp ratio
OPS.append(('div', 2, ('P', 'P'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # pressure ratio
OPS.append(('div', 2, ('N', 'N'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # speed ratio
OPS.append(('div', 2, ('T', 'P'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # T/P ~ specific vol
OPS.append(('div', 2, ('F', 'N'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # flow coefficient
OPS.append(('div', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a / (b + 1e-8)))  # ratio of ratios
OPS.append(('div', 2, ('F', 'F'), 'S', lambda a, b, fs: a / (b + 1e-8)))    # flow ratio
OPS.append(('div', 2, ('Ctrl', 'N'), 'S', lambda a, b, fs: a / (b + 1e-8)))

# Cross-type products
OPS.append(('mul', 2, ('T', 'N'), 'S', lambda a, b, fs: a * b))   # performance param
OPS.append(('mul', 2, ('P', 'N'), 'S', lambda a, b, fs: a * b))   # power proxy
OPS.append(('mul', 2, ('S', 'T'), 'T', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'P'), 'P', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'N'), 'N', lambda a, b, fs: a * b))
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))

# Cross-type subtraction -> Scalar (physically meaningful differences)
OPS.append(('sub_x', 2, ('T', 'T'), 'S', lambda a, b, fs: a - b))  # T30-T24 = compressor rise
OPS.append(('sub_x', 2, ('N', 'N'), 'S', lambda a, b, fs: a - b))  # Nf-NRf = correction err
OPS.append(('sub_x', 2, ('P', 'P'), 'S', lambda a, b, fs: a - b))  # P30-Ps30 = dynamic P

# Build lookup
UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1] == 1 and o[2] == (t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1] == 2 and o[2] == key]

n_sigs = sum(1 for v in BINARY_OPS.values() if v)
print(f"    Type system: {len(TYPES)} types, {len(TERMINALS)} terminals, "
      f"{len(OPS)} operator signatures")

# -- Expression Tree ----------------------------------------------------------
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
                left = self.children[0].evaluate(signals, fs, _depth + 1)
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


# -- Tree Generation ----------------------------------------------------------
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


def random_cross_type_tree(max_depth=MAX_DEPTH):
    """Generate a tree whose root MUST be a cross-type binary op (T/P, T*N, etc.).
    This forces exploration of multi-domain feature space."""
    cross_ops = []
    for (t1, t2), ops in BINARY_OPS.items():
        if t1 != t2 and t1 != 'S' and t2 != 'S':  # truly cross-type
            for name, func, out in ops:
                cross_ops.append((name, func, out, t1, t2))
    if not cross_ops:
        return random_tree(max_depth)
    name, func, out, t1, t2 = random.choice(cross_ops)
    left = random_tree(max_depth - 1, t1)
    right = random_tree(max_depth - 1, t2)
    return simplify(Node('binary', name, out, func, [left, right]))


# -- Simplification -----------------------------------------------------------
def simplify(tree):
    """Remove algebraic redundancies: abs(abs(x))=abs(x), neg(neg(x))=x, etc."""
    tree = tree.copy()
    if tree.kind == 'unary' and tree.children:
        tree.children[0] = simplify(tree.children[0])
        child = tree.children[0]
        # abs(abs(x)) = abs(x)
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'abs':
            return child
        # neg(neg(x)) = x
        if tree.name == 'neg' and child.kind == 'unary' and child.name == 'neg':
            return child.children[0]
        # abs(neg(x)) = abs(x)
        if tree.name == 'abs' and child.kind == 'unary' and child.name == 'neg':
            tree.children[0] = child.children[0]
        # any_op(const) where const is known -> collapse
        if child.kind == 'const':
            val = child.const_val
            if tree.name == 'abs':
                return Node('const', f'c{abs(val)}', 'S', const_val=abs(val))
            if tree.name == 'neg':
                return Node('const', f'c{-val}', 'S', const_val=-val)
    if tree.kind == 'binary' and len(tree.children) == 2:
        tree.children[0] = simplify(tree.children[0])
        tree.children[1] = simplify(tree.children[1])
        # c * expr = expr (when c=1.0)
        if tree.name == 'mul':
            for i in range(2):
                if (tree.children[i].kind == 'const'
                        and abs(tree.children[i].const_val - 1.0) < 1e-8):
                    return tree.children[1 - i]
    return tree


# -- Tree Manipulation --------------------------------------------------------
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
                n1_copy = n1.copy()
                n2_copy = n2.copy()
                par1.children[idx1] = n2_copy
                par2.children[idx2] = n1_copy
                return t1, t2
    return t1, t2


# -- Fitness Function (v5 with split-half reliability) ------------------------
SUMMARIES = {
    'std': lambda a: np.std(a),
    'rms': lambda a: np.sqrt(np.mean(a ** 2)),
    'mean_abs': lambda a: np.mean(np.abs(a)),
}


def evaluate_feature(tree, trials, fs, summary='std'):
    sfunc = SUMMARIES[summary]
    scalars = []
    for trial in trials:
        result = tree.evaluate(trial, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 10:
            return None
        scalars.append(sfunc(finite))
    return np.array(scalars)


def split_half_reliability(tree, trials, fs, summary='std'):
    sfunc = SUMMARIES[summary]
    first_half, second_half = [], []
    for trial in trials:
        result = tree.evaluate(trial, fs)
        if result is None:
            return 0.0
        finite = result[np.isfinite(result)]
        if len(finite) < 16:
            return 0.0
        mid = len(finite) // 2
        first_half.append(sfunc(finite[:mid]))
        second_half.append(sfunc(finite[mid:]))
    if len(first_half) < 10 or np.std(first_half) < 1e-10 or np.std(second_half) < 1e-10:
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

    # Tightness
    c, _ = np.histogram(vals, bins=min(20, len(vals) // 3))
    p = c / c.sum()
    p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)

    # Stationarity
    chunks = np.array_split(vals, 5)
    cmeans = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(cmeans) < 2 or abs(np.mean(cmeans)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(cmeans) / (abs(np.mean(cmeans)) + 1e-8))

    # Headroom
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))

    return (tightness + stationarity + headroom + reliability) / 4.0


def fitness_full(tree, train_trials, fs):
    """Evaluate fitness across all summaries, return best."""
    best_score, best_smry, best_rel = 0.0, 'std', 0.0
    for smry in SUMMARIES:
        vals = evaluate_feature(tree, train_trials, fs, smry)
        if vals is None:
            continue
        rel = split_half_reliability(tree, train_trials, fs, smry)
        sc = composite_v5(vals, rel)

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

        if rel < 0.3:
            sc = sc * penalty * 0.7
        else:
            sc = sc * penalty

        if sc > best_score:
            best_score, best_smry, best_rel = sc, smry, rel

    return best_score, best_smry, best_rel


# -- GP Evolution -------------------------------------------------------------
def expr_hash(tree):
    return hash(tree.expr_str()[:60])


def deduplicate(population, scores, trials, fs, threshold=0.85):
    n_check = min(80, len(population))
    vectors = []
    for i in range(n_check):
        v = evaluate_feature(population[i], trials, fs)
        vectors.append(v if v is not None else np.zeros(len(trials)))
    keep = [True] * n_check
    for i in range(1, n_check):
        if not keep[i]:
            continue
        for j in range(i):
            if not keep[j]:
                continue
            if (len(vectors[i]) == len(vectors[j])
                    and np.std(vectors[i]) > 1e-10
                    and np.std(vectors[j]) > 1e-10):
                r = abs(np.corrcoef(vectors[i], vectors[j])[0, 1])
                if np.isfinite(r) and r > threshold:
                    keep[i] = False
                    break
    new_pop = [population[i] for i in range(n_check) if keep[i]]
    new_sc = [scores[i] for i in range(n_check) if keep[i]]
    new_pop.extend(population[n_check:])
    new_sc.extend(scores[n_check:])
    return new_pop, new_sc


def run_gp(trials, fs):
    print(f"\n[4] Running GP: pop={POP_SIZE}, gen={GENERATIONS}, "
          f"depth<={MAX_DEPTH}, {len(trials)} trials")

    # Seed 25% of initial population with cross-type trees
    n_cross_init = POP_SIZE // 4
    population = ([random_cross_type_tree(MAX_DEPTH) for _ in range(n_cross_init)] +
                  [simplify(random_tree(MAX_DEPTH)) for _ in range(POP_SIZE - n_cross_init)])
    scores, summaries, reliabilities = [], [], []
    for t in population:
        sc, sm, rl = fitness_full(t, trials, fs)
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
            expr = paired[0][3].expr_str()[:50]
            top10_rel = np.mean([paired[i][2] for i in range(min(10, len(paired)))])
            # Count type families in top 20
            type_fams = set()
            for idx_p in range(min(20, len(paired))):
                estr = paired[idx_p][3].expr_str()
                for term, ttype in TERMINALS.items():
                    if term in estr:
                        type_fams.add(ttype)
            print(f"    Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"rel={paired[0][2]:.3f}  top10_rel={top10_rel:.3f}  "
                  f"types={len(type_fams)}/{len(TYPES)-1}  "
                  f"({elapsed:.0f}s)  {expr}")

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

        # Inject best-ever
        if best_ever[1] is not None:
            new_pop.append(best_ever[1].copy())
            new_sc.append(best_ever[0])
            new_sm.append(best_ever[2])
            new_rl.append(best_ever[3])

        # Immigration: half random, half forced cross-type
        n_cross_imm = n_immigrant // 2
        for idx_imm in range(n_immigrant):
            if idx_imm < n_cross_imm:
                imm = random_cross_type_tree(MAX_DEPTH)
            else:
                imm = simplify(random_tree(MAX_DEPTH))
            s, sm, rl = fitness_full(imm, trials, fs)
            new_pop.append(imm)
            new_sc.append(s)
            new_sm.append(sm)
            new_rl.append(rl)

        # Fill via tournament
        while len(new_pop) < POP_SIZE:
            def tournament():
                idxs = random.sample(range(len(paired)), min(TOURNAMENT_K, len(paired)))
                return paired[max(idxs, key=lambda i: paired[i][0])][3]

            r = random.random()
            if r < CROSSOVER_RATE:
                c1, c2 = crossover(tournament(), tournament())
                c1, c2 = simplify(c1), simplify(c2)
                s1, sm1, rl1 = fitness_full(c1, trials, fs)
                new_pop.append(c1)
                new_sc.append(s1)
                new_sm.append(sm1)
                new_rl.append(rl1)
                if len(new_pop) < POP_SIZE:
                    s2, sm2, rl2 = fitness_full(c2, trials, fs)
                    new_pop.append(c2)
                    new_sc.append(s2)
                    new_sm.append(sm2)
                    new_rl.append(rl2)
            elif r < CROSSOVER_RATE + MUTATION_RATE:
                child = simplify(mutate(tournament()))
                s, sm, rl = fitness_full(child, trials, fs)
                new_pop.append(child)
                new_sc.append(s)
                new_sm.append(sm)
                new_rl.append(rl)
            else:
                p = tournament()
                new_pop.append(p.copy())
                s, sm, rl = fitness_full(p, trials, fs)
                new_sc.append(s)
                new_sm.append(sm)
                new_rl.append(rl)

        population = new_pop[:POP_SIZE]
        scores = new_sc[:POP_SIZE]
        summaries = new_sm[:POP_SIZE]
        reliabilities = new_rl[:POP_SIZE]

        # Multi-stage dedup (at gens 20, 40, 60) to prevent monoculture
        if gen in (20, 40, 60):
            paired = sorted(zip(scores, population), key=lambda x: -x[0])
            population = [t for _, t in paired]
            scores = [s for s, _ in paired]
            population, scores = deduplicate(population, scores, trials, fs)
            summaries_new, rels_new = [], []
            for t in population:
                _, sm, rl = fitness_full(t, trials, fs)
                summaries_new.append(sm)
                rels_new.append(rl)
            summaries = summaries_new
            reliabilities = rels_new
            n_refill = POP_SIZE - len(population)
            for ri in range(n_refill):
                if ri < n_refill // 2:
                    imm = random_cross_type_tree(MAX_DEPTH)
                else:
                    imm = simplify(random_tree(MAX_DEPTH))
                s, sm, rl = fitness_full(imm, trials, fs)
                population.append(imm)
                scores.append(s)
                summaries.append(sm)
                reliabilities.append(rl)

    # Final sort + dedup
    paired = sorted(zip(scores, summaries, reliabilities, population),
                    key=lambda x: -x[0])
    population = [t for _, _, _, t in paired]
    scores = [s for s, _, _, _ in paired]
    population, scores = deduplicate(population, scores, trials, fs)
    # Recompute summaries for deduped population
    final_results = []
    for i, tree in enumerate(population):
        sc, sm, rl = fitness_full(tree, trials, fs)
        final_results.append((sc, sm, rl, tree))
    final_results.sort(key=lambda x: -x[0])

    elapsed = time.time() - t0
    print(f"\n    GP complete: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    if final_results:
        print(f"    Best: {final_results[0][0]:.4f} [{final_results[0][1]}] "
              f"-- {final_results[0][3].expr_str()[:70]}")
    print(f"    Unique features after dedup: {len(final_results)}")
    return final_results


# -- Timeline Utilities -------------------------------------------------------
def rolling_std_full(a, w):
    """O(n) rolling std using cumsum trick."""
    n = len(a)
    if n < w:
        return np.full(n, np.nan)
    cs = np.concatenate([[0], np.cumsum(a)])
    cs2 = np.concatenate([[0], np.cumsum(a ** 2)])
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = np.maximum(0, s2 / w - (s / w) ** 2)
    result = np.full(n, np.nan)
    result[w - 1:] = np.sqrt(var)
    return result


def rolling_mean_full(a, w):
    n = len(a)
    if n < w:
        return np.full(n, np.nan)
    cs = np.concatenate([[0], np.cumsum(a)])
    result = np.full(n, np.nan)
    result[w - 1:] = (cs[w:] - cs[:-w]) / w
    return result


def rolling_rms_full(a, w):
    n = len(a)
    if n < w:
        return np.full(n, np.nan)
    cs2 = np.concatenate([[0], np.cumsum(a ** 2)])
    s2 = cs2[w:] - cs2[:-w]
    result = np.full(n, np.nan)
    result[w - 1:] = np.sqrt(s2 / w)
    return result


def rolling_mean_abs_full(a, w):
    return rolling_mean_full(np.abs(a), w)


ROLLING_FUNCS = {
    'std': rolling_std_full,
    'rms': rolling_rms_full,
    'mean_abs': rolling_mean_abs_full,
}


def compute_gp_timeline(tree, summary, engine_sensors, window):
    """Evaluate GP tree on full engine signal, compute rolling summary."""
    result = tree.evaluate(engine_sensors, FS)
    if result is None:
        return np.full(len(engine_sensors[FIRST_SIGNAL]), np.nan)
    clean = np.where(np.isfinite(result), result, 0)
    return ROLLING_FUNCS[summary](clean, window)


def compute_gp_timelines_all(tree, engine_sensors, window):
    """Compute timelines for ALL summary types. Returns dict summary->timeline."""
    result = tree.evaluate(engine_sensors, FS)
    if result is None:
        nan_arr = np.full(len(engine_sensors[FIRST_SIGNAL]), np.nan)
        return {s: nan_arr for s in ROLLING_FUNCS}
    clean = np.where(np.isfinite(result), result, 0)
    return {s: func(clean, window) for s, func in ROLLING_FUNCS.items()}


def find_first_detection(timeline, healthy_mu, healthy_std,
                         threshold=THRESHOLD, min_consec=MIN_CONSEC):
    if healthy_std < 1e-10:
        healthy_std = 1e-10
    z_scores = np.abs(timeline - healthy_mu) / healthy_std
    count = 0
    for i, z in enumerate(z_scores):
        if np.isfinite(z) and z > threshold:
            count += 1
            if count >= min_consec:
                return i - min_consec + 1
        else:
            count = 0
    return len(timeline)  # never detected


# -- Baselines ----------------------------------------------------------------
def build_baseline_timelines(engine_sensors, window):
    """Compute all baseline feature timelines for one engine. Returns dict."""
    timelines = {}

    # Individual sensor rolling stats
    for s in USEFUL_SENSORS:
        sig = engine_sensors[s]
        timelines[f'{s}_std'] = rolling_std_full(sig, window)
        timelines[f'{s}_mean'] = rolling_mean_full(sig, window)

    # Physical ratios (rolling mean of pointwise ratio)
    def safe_ratio(a, b):
        return a / (b + 1e-8)

    pairs = [
        ('T30_T24_ratio', 'T30', 'T24'),
        ('T50_T30_ratio', 'T50', 'T30'),
        ('P30_Ps30_ratio', 'P30', 'Ps30'),
        ('Nf_Nc_ratio', 'Nf', 'Nc'),
        ('NRf_NRc_ratio', 'NRf', 'NRc'),
        ('W31_W32_ratio', 'W31', 'W32'),
    ]
    for name, a, b in pairs:
        timelines[name] = rolling_mean_full(
            safe_ratio(engine_sensors[a], engine_sensors[b]), window)

    # Physical differences
    diffs = [
        ('T30_T24_diff', 'T30', 'T24'),     # compressor temperature rise
        ('Nf_NRf_diff', 'Nf', 'NRf'),       # speed correction deviation
        ('P30_Ps30_diff', 'P30', 'Ps30'),    # dynamic pressure
    ]
    for name, a, b in diffs:
        timelines[name] = rolling_mean_full(
            engine_sensors[a] - engine_sensors[b], window)

    # Products
    timelines['phi_x_Ps30'] = rolling_mean_full(
        engine_sensors['phi'] * engine_sensors['Ps30'], window)
    timelines['T30_x_Nc'] = rolling_mean_full(
        engine_sensors['T30'] * engine_sensors['Nc'], window)

    return timelines


# -- Main Execution -----------------------------------------------------------
gp_results = run_gp(train_windows, FS)

# -- [5] Compute timelines across all engines ---------------------------------
print(f"\n[5] Computing timelines across all {len(engines)} engines...")
t0 = time.time()

TOP_N = min(50, len(gp_results))
gp_features = []
for rank, (score, smry, rel, tree) in enumerate(gp_results[:TOP_N]):
    gp_features.append({
        'rank': rank + 1,
        'score': score,
        'summary': smry,
        'reliability': rel,
        'expr': tree.expr_str(),
        'tree': tree,
        'depth': tree.depth(),
    })

# For each engine, compute detection for all features
all_gp_detections = {i: [] for i in range(len(gp_features))}  # gp_idx -> list of det_pcts
all_bl_detections = {}  # bl_name -> list of det_pcts

first_engine = True
for uid, eng in engines.items():
    n_cyc = eng['n_cycles']
    sensors = eng['sensors']
    healthy_end = max(WINDOW + 10, int(n_cyc * HEALTHY_FRAC))

    # GP features — try all summary types, keep best detection per feature
    for gi, gf in enumerate(gp_features):
        all_tls = compute_gp_timelines_all(gf['tree'], sensors, WINDOW)
        best_det_pct = 100.0
        for smry_name, tl in all_tls.items():
            h_vals = tl[WINDOW - 1:healthy_end]
            h_vals = h_vals[np.isfinite(h_vals)]
            if len(h_vals) < 5:
                continue
            h_mu, h_std = np.mean(h_vals), np.std(h_vals)
            det_idx = find_first_detection(tl, h_mu, h_std)
            det_pct = det_idx / n_cyc * 100.0
            if det_pct < best_det_pct:
                best_det_pct = det_pct
        all_gp_detections[gi].append(min(best_det_pct, 100.0))

    # Baselines
    bl_tls = build_baseline_timelines(sensors, WINDOW)
    for bname, tl in bl_tls.items():
        if bname not in all_bl_detections:
            all_bl_detections[bname] = []
        h_vals = tl[WINDOW - 1:healthy_end]
        h_vals = h_vals[np.isfinite(h_vals)]
        if len(h_vals) < 5:
            all_bl_detections[bname].append(100.0)
            continue
        h_mu, h_std = np.mean(h_vals), np.std(h_vals)
        det_idx = find_first_detection(tl, h_mu, h_std)
        det_pct = det_idx / n_cyc * 100.0
        all_bl_detections[bname].append(min(det_pct, 100.0))

    if first_engine:
        print(f"    Engine {uid} done ({n_cyc} cycles). "
              f"Processing remaining...", flush=True)
        first_engine = False

print(f"    All engines processed in {time.time()-t0:.1f}s")

# -- [6] Aggregate Results ----------------------------------------------------
print(f"\n[6] RESULTS")
print("=" * 90)

# GP summary: median detection % per feature
gp_summary = []
for gi, gf in enumerate(gp_features):
    dets = all_gp_detections[gi]
    det_arr = np.array(dets)
    detected = np.sum(det_arr < 100.0)
    median_pct = np.median(det_arr)
    mean_pct = np.mean(det_arr)
    gp_summary.append({
        **gf,
        'median_det_pct': median_pct,
        'mean_det_pct': mean_pct,
        'detection_rate': detected / len(dets) * 100.0,
        'n_detected': int(detected),
        'all_dets': det_arr,
    })

# Baseline summary
bl_summary = []
for bname, dets in all_bl_detections.items():
    det_arr = np.array(dets)
    detected = np.sum(det_arr < 100.0)
    bl_summary.append({
        'name': bname,
        'median_det_pct': np.median(det_arr),
        'mean_det_pct': np.mean(det_arr),
        'detection_rate': detected / len(dets) * 100.0,
        'n_detected': int(detected),
    })

# Sort by median detection %
gp_by_det = sorted(gp_summary, key=lambda x: x['median_det_pct'])
bl_by_det = sorted(bl_summary, key=lambda x: x['median_det_pct'])

# Print baselines
print(f"\n  BASELINE DETECTION (sorted by median detection %, lower = earlier = better):")
print(f"  {'Feature':25s} {'Median%':>8s} {'Mean%':>8s} {'DetRate':>8s} {'Engines':>8s}")
print("  " + "-" * 62)
for b in bl_by_det[:20]:
    print(f"  {b['name']:25s} {b['median_det_pct']:7.1f}% {b['mean_det_pct']:7.1f}% "
          f"{b['detection_rate']:7.1f}% {b['n_detected']:5d}/100")

# Print GP features
print(f"\n  GP FEATURES (sorted by median detection %, lower = earlier = better):")
print(f"  {'#':>3s} {'Expression':40s} {'Smry':>5s} {'Rel':>5s} "
      f"{'Med%':>6s} {'Mean%':>7s} {'Rate':>6s}")
print("  " + "-" * 80)
for gf in gp_by_det[:20]:
    expr = gf['expr'][:38]
    print(f"  {gf['rank']:3d} {expr:40s} {gf['summary']:>5s} {gf['reliability']:5.2f} "
          f"{gf['median_det_pct']:5.1f}% {gf['mean_det_pct']:6.1f}% "
          f"{gf['detection_rate']:5.1f}%")

# Head-to-head
best_gp = gp_by_det[0] if gp_by_det else None
best_bl = bl_by_det[0] if bl_by_det else None

print(f"\n  EARLY DETECTION HEAD-TO-HEAD:")
print(f"  {'Method':.<12s} {'Feature':40s} {'Median%':>8s} {'DetRate':>8s}")
print("  " + "-" * 72)
if best_gp:
    print(f"  {'GP':.<12s} {best_gp['expr'][:38]:40s} "
          f"{best_gp['median_det_pct']:7.1f}% {best_gp['detection_rate']:7.1f}%")
if best_bl:
    print(f"  {'Baseline':.<12s} {best_bl['name']:40s} "
          f"{best_bl['median_det_pct']:7.1f}% {best_bl['detection_rate']:7.1f}%")

if best_gp and best_bl:
    gp_med = best_gp['median_det_pct']
    bl_med = best_bl['median_det_pct']
    if gp_med < bl_med - 1.0:
        # Compute lead in cycles (median engine has ~200 cycles)
        avg_life = np.mean(life_lengths)
        lead_pct = bl_med - gp_med
        lead_cycles = lead_pct / 100.0 * avg_life
        print(f"\n  >>> GP DETECTS {lead_pct:.1f}% EARLIER "
              f"(~{lead_cycles:.0f} cycles on avg-length engine)")
    elif bl_med < gp_med - 1.0:
        lead_pct = gp_med - bl_med
        avg_life = np.mean(life_lengths)
        lead_cycles = lead_pct / 100.0 * avg_life
        print(f"\n  >>> BASELINE DETECTS {lead_pct:.1f}% EARLIER "
              f"(~{lead_cycles:.0f} cycles on avg-length engine)")
    else:
        print(f"\n  >>> NEAR TIE (within 1% detection time)")

# Detection rate comparison
print(f"\n  DETECTION RATE (engines where degradation detected before failure):")
if gp_by_det:
    gp_rates = [gf['detection_rate'] for gf in gp_by_det[:10]]
    print(f"    Top-10 GP features:  avg {np.mean(gp_rates):.1f}% detection rate")
if bl_by_det:
    bl_rates = [b['detection_rate'] for b in bl_by_det[:10]]
    print(f"    Top-10 baselines:    avg {np.mean(bl_rates):.1f}% detection rate")

# Terminal usage
print(f"\n  TERMINAL USAGE IN TOP-10 GP FEATURES:")
signal_counts = Counter()
for gf in gp_by_det[:10]:
    for term in TERMINALS:
        if term in gf['expr']:
            signal_counts[term] += 1
for sig, count in signal_counts.most_common():
    ttype = TERMINALS[sig]
    print(f"    {sig:10s} ({ttype:>4s}): {count}/10")

# Cross-type features
n_cross = 0
for gf in gp_by_det[:10]:
    types_used = set()
    for term in TERMINALS:
        if term in gf['expr']:
            types_used.add(TERMINALS[term])
    if len(types_used) >= 2:
        n_cross += 1
print(f"    Cross-type features: {n_cross}/10")

# Summary
print("\n" + "=" * 90)
print("  SUMMARY: C-MAPSS FD001 Run-to-Failure Experiment")
print("=" * 90)
print(f"  100 engines, {min(life_lengths)}-{max(life_lengths)} cycles "
      f"(avg {np.mean(life_lengths):.0f})")
print(f"  GP trained on {len(train_windows)} healthy windows "
      f"(first {HEALTHY_FRAC*100:.0f}% of life)")
if best_gp:
    print(f"  Best GP detection: {best_gp['median_det_pct']:.1f}% median "
          f"({best_gp['detection_rate']:.0f}% engines)")
    print(f"    Feature: {best_gp['expr'][:65]}")
if best_bl:
    print(f"  Best baseline detection: {best_bl['median_det_pct']:.1f}% median "
          f"({best_bl['detection_rate']:.0f}% engines)")
    print(f"    Feature: {best_bl['name']}")
if best_gp and best_bl:
    if best_gp['median_det_pct'] < best_bl['median_det_pct'] - 1.0:
        avg_life = np.mean(life_lengths)
        lead = best_bl['median_det_pct'] - best_gp['median_det_pct']
        print(f"  GP WINS: {lead:.1f}% earlier detection "
              f"(~{lead/100*avg_life:.0f} cycles)")
    elif best_bl['median_det_pct'] < best_gp['median_det_pct'] - 1.0:
        lead = best_gp['median_det_pct'] - best_bl['median_det_pct']
        print(f"  Baseline wins: {lead:.1f}% earlier detection")
    else:
        print(f"  TIE: within 1% detection difference")

print(f"  {len(gp_features)} unique GP features evaluated")
print("=" * 90)
print("  Done.")
