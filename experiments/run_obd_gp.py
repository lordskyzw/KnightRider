"""
KnightRider OBD-II GP Experiment — Simulated Data
==================================================
Apply the typed GP framework to automotive OBD-II signals.
Train on healthy driving sessions only, evaluate against 3 fault types:
  1. Thermostat stuck open  (slow warmup, low steady temp)
  2. Intake/vacuum leak     (low VE, erratic idle, +trims)
  3. Catalyst degradation   (downstream O2 oscillates)

Type system follows DIAGNOSTICS_PRINCIPLES.md:
  R  = Rotation      (RPM)
  T  = Temperature   (coolant, IAT)
  F  = Flow          (MAF)
  P  = Position      (throttle)
  Ra = Ratio         (fuel trims, O2 voltages, VE-like)
  S  = Scalar        (derived / dimensionless)
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

# ── Constants ──────────────────────────────────────────────────────────────
DISP = 1.6       # engine displacement, liters
FS = 10.0         # 10 Hz sample rate (dt=0.1s)
DURATION = 300.0  # 5-minute sessions
DT = 0.1
STEPS = int(DURATION / DT)

N_HEALTHY = 40    # training sessions
N_TEST = 20       # test sessions per condition
N_HELD = 20       # held-out healthy for AUC

# GP parameters
POP_SIZE = 200
GENERATIONS = 80
MAX_DEPTH = 4
TOURNAMENT_K = 5
ELITE_FRAC = 0.05
CROSSOVER_RATE = 0.60
MUTATION_RATE = 0.30
IMMIGRATION_RATE = 0.10
RSTD_WINDOW = 30  # 3 seconds at 10Hz

# Summaries to try
SUMMARIES = ['std', 'mean', 'rms']

print("=" * 70)
print("  KnightRider OBD-II GP Experiment — Simulated Data")
print("=" * 70)

# ── Data Generation (from stage2_obd.py) ───────────────────────────────────
def gen_session(fault='none'):
    """Generate a 5-minute OBD-II driving session."""
    steps = STEPS
    t = np.linspace(0, DURATION, steps)

    # Mixed driving profile
    tc = np.zeros(steps)
    for i in range(steps):
        p = (t[i] % 60) / 60
        if p < 0.2:   tc[i] = 3
        elif p < 0.5: tc[i] = 20 + 15 * np.sin(p * 10)
        elif p < 0.8: tc[i] = 40
        else:          tc[i] = 5
    throttle = np.clip(tc + np.random.normal(0, 1.5, steps), 0, 100)

    # RPM
    rpm = np.zeros(steps); rpm[0] = 800
    lag = 0.85 if fault != 'intake_leak' else 0.92
    for i in range(1, steps):
        rpm[i] = rpm[i-1] * lag + (800 + throttle[i] * 50) * (1 - lag)
    rpm += np.random.normal(0, 15, steps)
    if fault == 'intake_leak':
        rpm[throttle < 5] += np.random.normal(0, 60, np.sum(throttle < 5))

    # Coolant temperature
    cool = np.zeros(steps); cool[0] = 25
    steady = 90 if fault != 'thermostat' else 72
    cr = 0.015 if fault != 'thermostat' else 0.006
    for i in range(1, steps):
        cool[i] = cool[i-1] + cr * (steady - cool[i-1])
    cool += np.random.normal(0, 0.3, steps)

    # MAF (Mass Air Flow)
    ve_b = 0.85 if fault != 'intake_leak' else 0.62
    maf = (rpm/60) * DISP * ve_b / 2 * 1.225 * (throttle/100 + 0.15)
    maf = np.clip(maf, 1, 250) + np.random.normal(0, 0.5, steps)

    # Fuel trims
    stft = np.random.normal(0, 2, steps)
    ltft = np.ones(steps) * (0 if fault != 'intake_leak' else 12) + np.random.normal(0, 0.5, steps)

    # O2 sensors
    o2u = np.clip(0.45 + 0.35*np.sin(2*np.pi*t) + np.random.normal(0, 0.05, steps), 0, 1)
    if fault == 'catalyst':
        o2d = np.clip(0.45 + 0.30*np.sin(2*np.pi*t) + np.random.normal(0, 0.03, steps), 0, 1)
    else:
        o2d = np.clip(0.45 + np.random.normal(0, 0.03, steps), 0, 1)

    # Intake air temperature
    iat = 30 + throttle * 0.1 + np.random.normal(0, 0.5, steps)

    return {
        'rpm': rpm, 'coolant': cool, 'maf': maf, 'throttle': throttle,
        'stft': stft, 'ltft': ltft, 'o2_up': o2u, 'o2_down': o2d, 'iat': iat,
        'time': t
    }

print("\n[1] Generating data...")
t0 = time.time()
healthy_train = [gen_session('none') for _ in range(N_HEALTHY)]
healthy_held  = [gen_session('none') for _ in range(N_HELD)]
fault_thermo  = [gen_session('thermostat') for _ in range(N_TEST)]
fault_intake  = [gen_session('intake_leak') for _ in range(N_TEST)]
fault_cat     = [gen_session('catalyst') for _ in range(N_TEST)]
print(f"    {N_HEALTHY} train + {N_HELD} held-out healthy + {N_TEST}x3 fault = "
      f"{N_HEALTHY + N_HELD + N_TEST*3} sessions ({time.time()-t0:.1f}s)")

# ── Automotive Type System ─────────────────────────────────────────────────
# R=Rotation, T=Temperature, F=Flow, P=Position, Ra=Ratio, S=Scalar
TYPES = ('R', 'T', 'F', 'P', 'Ra', 'S')

TERMINALS = {
    'rpm':      'R',
    'coolant':  'T',
    'iat':      'T',
    'maf':      'F',
    'throttle': 'P',
    'stft':     'Ra',
    'ltft':     'Ra',
    'o2_up':    'Ra',
    'o2_down':  'Ra',
}
CONSTS = [0.5, 1.0, 2.0, 10.0]
FIRST_SIGNAL = list(TERMINALS.keys())[0]  # for const array sizing

# ── Operators ──────────────────────────────────────────────────────────────
OPS = []

# Temporal derivative: d/dt on all signal types
for t in ('R', 'T', 'F', 'P', 'Ra'):
    OPS.append(('ddt', 1, (t,), t, lambda a, fs: np.gradient(a, 1/fs)))

# Rolling std (variability) — fully vectorized numpy
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

for t in ('R', 'T', 'F', 'P', 'Ra'):
    OPS.append(('rstd', 1, (t,), t, lambda a, fs: _rstd(a, fs)))

# Rolling mean (smoothing) — fully vectorized numpy
def _rmean(a, fs, w=RSTD_WINDOW):
    n = len(a)
    if n < w: return a.copy()
    cs = np.concatenate([[0], np.cumsum(a)])
    rm = (cs[w:] - cs[:-w]) / w
    result = np.zeros(n)
    result[w-1:] = rm
    # Fill early values with expanding mean
    result[:w-1] = np.cumsum(a[:w-1]) / np.arange(1, w)
    return result

for t in ('R', 'T', 'F', 'P', 'Ra'):
    OPS.append(('rmean', 1, (t,), t, lambda a, fs: _rmean(a, fs)))

# Absolute value
for t in ('R', 'T', 'F', 'P', 'Ra', 'S'):
    OPS.append(('abs', 1, (t,), t, lambda a, fs: np.abs(a)))

# Negation
for t in TYPES:
    OPS.append(('neg', 1, (t,), t, lambda a, fs: -a))

# Same-type addition/subtraction
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Cross-type ratios (the diagnostic money operators)
# Flow / Rotation → Ratio  (volumetric efficiency proxy)
OPS.append(('div', 2, ('F', 'R'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
# Rotation / Flow → Ratio
OPS.append(('div', 2, ('R', 'F'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
# Temperature / Temperature → Scalar (thermal ratio)
OPS.append(('div', 2, ('T', 'T'), 'S', lambda a, b, fs: a / (b + 1e-8)))
# Ratio / Ratio → Scalar (e.g. O2 upstream / downstream)
OPS.append(('div', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a / (b + 1e-8)))
# Flow / Position → Ratio (airflow per throttle opening)
OPS.append(('div', 2, ('F', 'P'), 'Ra', lambda a, b, fs: a / (b + 1e-8)))
# Same-type ratio → Scalar
for t in TYPES:
    if t != 'S':
        OPS.append(('div', 2, (t, t), 'S', lambda a, b, fs: a / (b + 1e-8)))

# Cross-type products
# Scalar × anything → same type
for t in TYPES:
    OPS.append(('mul', 2, ('S', t), t, lambda a, b, fs: a * b))
# Position × Rotation → Flow-like (throttle × RPM ~ load proxy)
OPS.append(('mul', 2, ('P', 'R'), 'F', lambda a, b, fs: a * b))

# Cross-type differences → Scalar (for inter-signal comparison)
# Temperature - Temperature → Scalar (thermal delta)
OPS.append(('sub_cross', 2, ('T', 'T'), 'S', lambda a, b, fs: a - b))
# Ratio - Ratio → Scalar (trim delta, O2 delta)
OPS.append(('sub_cross', 2, ('Ra', 'Ra'), 'S', lambda a, b, fs: a - b))

# Build lookup tables
UNARY_OPS = {t: [(o[0], o[4], o[3]) for o in OPS if o[1]==1 and o[2]==(t,)]
             for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1]==2 and o[2]==key]

print(f"    Type system: {len(TYPES)} types, {len(TERMINALS)} terminals, "
      f"{len(OPS)} operator signatures")

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
        if _depth > 10: return None  # prevent runaway recursion
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
        """Non-recursive copy to avoid stack overflow on deep/cyclic trees."""
        new = Node(self.kind, self.name, self.out_type, self.func, None, self.const_val)
        new.children = [c.copy() for c in self.children] if self.children else []
        return new


# ── Tree Generation ────────────────────────────────────────────────────────
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
    # Try unary
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
    # Try binary
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


# ── Tree Manipulation ──────────────────────────────────────────────────────
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
                # Copy subtrees before swapping to avoid circular refs
                n1_copy = n1.copy()
                n2_copy = n2.copy()
                par1.children[idx1] = n2_copy
                par2.children[idx2] = n1_copy
                return t1, t2
    return t1, t2


# ── Fitness Function (v5: with split-half reliability) ─────────────────────
def evaluate_feature(tree, sessions, fs, summary='std'):
    """Evaluate tree on sessions, return one scalar per session."""
    scalars = []
    for sess in sessions:
        result = tree.evaluate(sess, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 50:
            return None
        if summary == 'std':
            scalars.append(np.std(finite))
        elif summary == 'mean':
            scalars.append(np.mean(finite))
        elif summary == 'rms':
            scalars.append(np.sqrt(np.mean(finite**2)))
    return np.array(scalars)

def split_half_reliability(tree, sessions, fs, summary='std'):
    """Compute split-half reliability: correlation between first/second half summaries."""
    first_half, second_half = [], []
    for sess in sessions:
        result = tree.evaluate(sess, fs)
        if result is None: return 0.0
        finite = result[np.isfinite(result)]
        if len(finite) < 100: return 0.0
        mid = len(finite) // 2
        h1, h2 = finite[:mid], finite[mid:]
        if summary == 'std':
            first_half.append(np.std(h1)); second_half.append(np.std(h2))
        elif summary == 'mean':
            first_half.append(np.mean(h1)); second_half.append(np.mean(h2))
        elif summary == 'rms':
            first_half.append(np.sqrt(np.mean(h1**2))); second_half.append(np.sqrt(np.mean(h2**2)))
    if len(first_half) < 10 or np.std(first_half) < 1e-10 or np.std(second_half) < 1e-10:
        return 0.0
    r = np.corrcoef(first_half, second_half)[0, 1]
    return max(0.0, r) if np.isfinite(r) else 0.0

def composite_fitness(tree, sessions, fs, summary='std'):
    """v5 fitness: (tightness + stationarity + headroom + reliability) / 4."""
    vals = evaluate_feature(tree, sessions, fs, summary)
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
    reliability = split_half_reliability(tree, sessions, fs, summary)

    # Smoothing-at-root penalty (rstd and rmean both game tightness)
    penalty = 1.0
    if tree.kind == 'unary' and tree.name in ('rstd', 'rmean'):
        penalty = 0.5
    # Nested smoothing penalty: count depth of consecutive rmean/rstd
    node = tree
    smooth_depth = 0
    while node.kind == 'unary' and node.name in ('rmean', 'rstd'):
        smooth_depth += 1
        node = node.children[0]
    if smooth_depth >= 2:
        penalty *= 0.5  # heavy penalty for rmean(rmean(...))

    # Reliability gate: if R < 0.3, this is likely noise/gaming
    if reliability < 0.3:
        score = penalty * (tightness + stationarity + headroom + reliability) / 4.0 * 0.5
    else:
        score = penalty * (tightness + stationarity + headroom + reliability) / 4.0

    components = {
        'tightness': tightness, 'stationarity': stationarity,
        'headroom': headroom, 'reliability': reliability,
        'composite': score
    }
    return score, components


# ── Deduplication ──────────────────────────────────────────────────────────
def deduplicate(population, scores, sessions, fs, threshold=0.95):
    """Remove features that are highly correlated with higher-scoring features."""
    if len(population) < 5:
        return population, scores

    # Compute feature vectors for top features
    n_check = min(30, len(population))
    vectors = []
    for i in range(n_check):
        v = evaluate_feature(population[i], sessions, fs)
        if v is not None and len(v) > 0:
            vectors.append(v)
        else:
            vectors.append(np.zeros(len(sessions)))

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
    # Add back unchecked remainder
    new_pop.extend(population[n_check:])
    new_scores.extend(scores[n_check:])
    return new_pop, new_scores


# ── GP Evolution ───────────────────────────────────────────────────────────
def run_gp(sessions, fs):
    """Evolve feature expressions from OBD-II healthy sessions."""
    print(f"\n[2] Running GP: pop={POP_SIZE}, gen={GENERATIONS}, "
          f"depth<={MAX_DEPTH}, {len(sessions)} sessions")

    # Try multiple summaries, keep best
    best_summary = 'std'
    best_score = 0

    population = [random_tree(MAX_DEPTH) for _ in range(POP_SIZE)]
    scores = []
    for t in population:
        s, _ = composite_fitness(t, sessions, fs, 'std')
        scores.append(s)

    n_elite = max(2, int(POP_SIZE * ELITE_FRAC))
    n_immigrant = max(2, int(POP_SIZE * IMMIGRATION_RATE))
    best_ever = (0.0, None, {})
    seen_hashes = set()

    # Hash-based diversity: track expression structure hashes
    def expr_hash(tree):
        """Rough structural hash to detect near-duplicates."""
        s = tree.expr_str()
        # Normalize: remove const values, collapse neg(neg(...))
        s = s.replace('neg(neg(', '(')
        # Truncate for hash (deep trees with same structure)
        return hash(s[:60])

    t0 = time.time()
    for gen in range(GENERATIONS):
        # Sort
        paired = sorted(zip(scores, population), key=lambda x: -x[0])

        if paired[0][0] > best_ever[0]:
            _, comps = composite_fitness(paired[0][1], sessions, fs)
            best_ever = (paired[0][0], paired[0][1].copy(), comps)

        if gen % 10 == 0:
            elapsed = time.time() - t0
            expr = paired[0][1].expr_str()[:65]
            print(f"    Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"median={paired[POP_SIZE//2][0]:.4f}  "
                  f"({elapsed:.0f}s)  {expr}")

        # Build new population with diversity enforcement
        seen_hashes = set()
        new_pop = []
        new_scores = []
        for s, t in paired[:n_elite]:
            h = expr_hash(t)
            if h not in seen_hashes:
                new_pop.append(t.copy())
                new_scores.append(s)
                seen_hashes.add(h)
        # Ensure we have at least 2 elites
        while len(new_pop) < 2:
            new_pop.append(paired[0][1].copy())
            new_scores.append(paired[0][0])

        # Immigration (fresh random blood)
        for _ in range(n_immigrant):
            imm = random_tree(MAX_DEPTH)
            s, _ = composite_fitness(imm, sessions, fs)
            new_pop.append(imm)
            new_scores.append(s)

        # Fill via tournament
        while len(new_pop) < POP_SIZE:
            def tournament():
                idxs = random.sample(range(len(paired)), min(TOURNAMENT_K, len(paired)))
                best_idx = max(idxs, key=lambda i: paired[i][0])
                return paired[best_idx][1]

            r = random.random()
            if r < CROSSOVER_RATE:
                p1, p2 = tournament(), tournament()
                c1, c2 = crossover(p1, p2)
                s1, _ = composite_fitness(c1, sessions, fs)
                new_pop.append(c1); new_scores.append(s1)
                if len(new_pop) < POP_SIZE:
                    s2, _ = composite_fitness(c2, sessions, fs)
                    new_pop.append(c2); new_scores.append(s2)
            elif r < CROSSOVER_RATE + MUTATION_RATE:
                child = mutate(tournament())
                s, _ = composite_fitness(child, sessions, fs)
                new_pop.append(child); new_scores.append(s)
            else:
                p = tournament()
                new_pop.append(p.copy())
                s, _ = composite_fitness(p, sessions, fs)
                new_scores.append(s)

        population = new_pop[:POP_SIZE]
        scores = new_scores[:POP_SIZE]

        # Periodic dedup
        if gen > 0 and gen % 25 == 0:
            paired = sorted(zip(scores, population), key=lambda x: -x[0])
            population = [t for _, t in paired]
            scores = [s for s, _ in paired]
            population, scores = deduplicate(population, scores, sessions, fs)
            while len(population) < POP_SIZE:
                imm = random_tree(MAX_DEPTH)
                s, _ = composite_fitness(imm, sessions, fs)
                population.append(imm); scores.append(s)

    # Final sort + dedup
    paired = sorted(zip(scores, population), key=lambda x: -x[0])
    population = [t for _, t in paired]
    scores = [s for s, _ in paired]
    population, scores = deduplicate(population, scores, sessions, fs)

    elapsed = time.time() - t0
    print(f"\n    GP complete: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"    Best: {scores[0]:.4f} -- {population[0].expr_str()[:80]}")
    print(f"    Unique features after dedup: {len(population)}")

    return list(zip(scores, population))


# ── Baseline Features ──────────────────────────────────────────────────────
def compute_baselines(sessions):
    """Hand-crafted OBD diagnostic features from stage2_obd.py."""
    results = {}
    for name, func in BASELINE_FEATURES.items():
        vals = []
        for sess in sessions:
            try:
                v = func(sess)
                vals.append(v if np.isfinite(v) else 0.0)
            except:
                vals.append(0.0)
        results[name] = np.array(vals)
    return results

# Define baseline features
BASELINE_FEATURES = {
    'rpm_mean':       lambda s: np.mean(s['rpm']),
    'rpm_std':        lambda s: np.std(s['rpm']),
    'coolant_final':  lambda s: s['coolant'][-1],
    'coolant_mean':   lambda s: np.mean(s['coolant']),
    'maf_mean':       lambda s: np.mean(s['maf']),
    'throttle_std':   lambda s: np.std(s['throttle']),
    'o2_up_std':      lambda s: np.std(s['o2_up']),
    'o2_down_std':    lambda s: np.std(s['o2_down']),
    'ltft_mean':      lambda s: np.mean(s['ltft']),
    'stft_std':       lambda s: np.std(s['stft']),
    'warmup_rate':    lambda s: (s['coolant'][min(600,len(s['coolant'])-1)] - s['coolant'][0]) / 60.0,
    've_mean':        lambda s: np.mean(s['maf'] / (s['rpm']/60 * 0.5 * DISP * 1.225 + 1e-6)),
    'total_ft_mean':  lambda s: np.mean(s['ltft'] + s['stft']),
    'cat_efficiency': lambda s: _cat_eff(s),
    'idle_stability': lambda s: np.std(s['rpm'][s['throttle'] < 5]) if np.sum(s['throttle'] < 5) > 10 else 0,
    'maf_per_rpm':    lambda s: np.mean(s['maf'] / (s['rpm'] + 1e-6)),
    'warmup_health':  lambda s: ((s['coolant'][min(600,len(s['coolant'])-1)] - s['coolant'][0]) / 60.0) / max(0.05, 0.3 * (90 - s['iat'][0]) / 65),
}

def _cat_eff(s):
    w = int(10 / DT)
    cr = []
    for j in range(0, len(s['o2_up']) - w, w):
        su = np.std(s['o2_up'][j:j+w])
        sd = np.std(s['o2_down'][j:j+w])
        if su > 0.01:
            cr.append(sd / su)
    return np.mean(cr) if cr else 0


# ── Evaluation (AUC) ──────────────────────────────────────────────────────
def compute_auc(healthy_vals, fault_vals):
    """AUC using z-score deviation from healthy mean/std."""
    mu, sigma = np.mean(healthy_vals), np.std(healthy_vals)
    if sigma < 1e-10: return 0.5

    h_z = np.abs((healthy_vals - mu) / sigma)
    f_z = np.abs((fault_vals - mu) / sigma)

    labels = np.concatenate([np.zeros(len(h_z)), np.ones(len(f_z))])
    scores = np.concatenate([h_z, f_z])

    # Simple AUC via sorting
    order = np.argsort(-scores)
    labels_sorted = labels[order]
    n_pos = labels_sorted.sum()
    n_neg = len(labels_sorted) - n_pos
    if n_pos == 0 or n_neg == 0: return 0.5

    tp = 0; fp = 0; auc = 0.0
    for lab in labels_sorted:
        if lab == 1:
            tp += 1
        else:
            fp += 1
            auc += tp
    return auc / (n_pos * n_neg)


# ── Main Experiment ────────────────────────────────────────────────────────
# Run GP
gp_results = run_gp(healthy_train, FS)

# Evaluate GP features
print("\n[3] Evaluating GP features...")
TOP_N = 50
gp_features = []
for score, tree in gp_results[:TOP_N]:
    _, comps = composite_fitness(tree, healthy_train, FS)
    h_vals = evaluate_feature(tree, healthy_held, FS)
    if h_vals is None: continue

    aucs = {}
    for fault_name, fault_sess in [('thermostat', fault_thermo),
                                    ('intake_leak', fault_intake),
                                    ('catalyst', fault_cat)]:
        f_vals = evaluate_feature(tree, fault_sess, FS)
        if f_vals is not None:
            aucs[fault_name] = compute_auc(h_vals, f_vals)
        else:
            aucs[fault_name] = 0.5

    rel = split_half_reliability(tree, healthy_train, FS)
    gp_features.append({
        'expr': tree.expr_str(),
        'score': score,
        'reliability': rel,
        'components': comps,
        'aucs': aucs,
        'avg_auc': np.mean(list(aucs.values())),
    })

# Evaluate baselines
print("[4] Evaluating baselines...")
baseline_h = compute_baselines(healthy_held)
baseline_results = []
for name in BASELINE_FEATURES:
    h_vals = baseline_h[name]
    aucs = {}
    for fault_name, fault_sess in [('thermostat', fault_thermo),
                                    ('intake_leak', fault_intake),
                                    ('catalyst', fault_cat)]:
        f_vals = compute_baselines(fault_sess)[name]
        aucs[fault_name] = compute_auc(h_vals, f_vals)

    baseline_results.append({
        'name': name,
        'aucs': aucs,
        'avg_auc': np.mean(list(aucs.values())),
    })

# ── Results ────────────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  RESULTS: GP vs BASELINES on Simulated OBD-II Data")
print("=" * 90)

# Best baseline per fault
print("\n  BASELINES:")
print(f"  {'Feature':25s} {'Thermo':>10s} {'Intake':>10s} {'Catalyst':>10s} {'Avg AUC':>10s}")
print("  " + "-" * 65)
for b in sorted(baseline_results, key=lambda x: -x['avg_auc']):
    print(f"  {b['name']:25s} {b['aucs']['thermostat']:10.3f} "
          f"{b['aucs']['intake_leak']:10.3f} {b['aucs']['catalyst']:10.3f} "
          f"{b['avg_auc']:10.3f}")

print("\n  TOP GP FEATURES:")
print(f"  {'#':>3s} {'Expression':50s} {'Score':>6s} {'R':>5s} "
      f"{'Thermo':>8s} {'Intake':>8s} {'Cataly':>8s} {'Avg':>7s}")
print("  " + "-" * 100)
for i, gf in enumerate(sorted(gp_features, key=lambda x: -x['avg_auc'])[:20]):
    expr = gf['expr'][:48]
    print(f"  {i+1:3d} {expr:50s} {gf['score']:6.3f} {gf['reliability']:5.2f} "
          f"{gf['aucs']['thermostat']:8.3f} {gf['aucs']['intake_leak']:8.3f} "
          f"{gf['aucs']['catalyst']:8.3f} {gf['avg_auc']:7.3f}")

# Head-to-head per fault
print("\n  HEAD-TO-HEAD (best per fault):")
print(f"  {'Fault':15s} {'Best GP':>10s} {'GP Expr':40s} {'Best Base':>10s} {'Base Name':25s} {'Winner':>10s}")
print("  " + "-" * 115)
for fault_name in ['thermostat', 'intake_leak', 'catalyst']:
    best_gp = max(gp_features, key=lambda x: x['aucs'][fault_name])
    best_bl = max(baseline_results, key=lambda x: x['aucs'][fault_name])
    gp_auc = best_gp['aucs'][fault_name]
    bl_auc = best_bl['aucs'][fault_name]
    winner = 'GP' if gp_auc > bl_auc + 0.01 else ('BASE' if bl_auc > gp_auc + 0.01 else 'TIE')
    expr = best_gp['expr'][:38]
    print(f"  {fault_name:15s} {gp_auc:10.3f} {expr:40s} {bl_auc:10.3f} {best_bl['name']:25s} {winner:>10s}")

# Population quality metrics
n_good = sum(1 for gf in gp_features if gf['avg_auc'] > 0.70)
n_reliable = sum(1 for gf in gp_features if gf['reliability'] > 0.5)
top10_rel = np.mean([gf['reliability'] for gf in sorted(gp_features, key=lambda x: -x['score'])[:10]])
top10_auc = np.mean([gf['avg_auc'] for gf in sorted(gp_features, key=lambda x: -x['avg_auc'])[:10]])

print(f"\n  POPULATION QUALITY:")
print(f"    GP features evaluated: {len(gp_features)}")
print(f"    Features with avg AUC > 0.70: {n_good}/{len(gp_features)} ({100*n_good/max(1,len(gp_features)):.0f}%)")
print(f"    Features with reliability > 0.5: {n_reliable}/{len(gp_features)}")
print(f"    Top-10 avg reliability: {top10_rel:.3f}")
print(f"    Top-10 avg AUC: {top10_auc:.3f}")

# What did the GP discover?
print("\n  WHAT THE GP DISCOVERED:")
print("  (Top features sorted by avg AUC)")
for i, gf in enumerate(sorted(gp_features, key=lambda x: -x['avg_auc'])[:10]):
    expr = gf['expr']
    # Identify what signals are used
    signals_used = set()
    for term in TERMINALS:
        if term in expr:
            signals_used.add(term)
    print(f"    {i+1:2d}. {expr[:70]}")
    print(f"        signals: {', '.join(sorted(signals_used))}  "
          f"AUC: {gf['avg_auc']:.3f}  R: {gf['reliability']:.2f}")

# Check if GP found cross-signal features
cross_signal = []
for gf in gp_features:
    signals_used = set()
    for term in TERMINALS:
        if term in gf['expr']:
            signals_used.add(term)
    if len(signals_used) >= 2:
        cross_signal.append(gf)

print(f"\n    Cross-signal features: {len(cross_signal)}/{len(gp_features)}")
if cross_signal:
    best_cross = max(cross_signal, key=lambda x: x['avg_auc'])
    print(f"    Best cross-signal: {best_cross['expr'][:70]}")
    print(f"        AUC: {best_cross['avg_auc']:.3f}")

print("\n" + "=" * 70)
print("  Experiment complete.")
print("=" * 70)
