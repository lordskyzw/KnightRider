"""
KnightRider Discovery Track — Type-Safe Genetic Programming Engine
==================================================================
Evolves diagnostic features from raw signals using typed operators.
NO hand-crafted features. NO fault data during evolution.
"""
import numpy as np
from copy import deepcopy
from scipy import stats
import random

# ── Type System ──────────────────────────────────────────────────────────────
# Physical types prevent nonsense like  sin(energy) or  angle + velocity
TYPES = ('A', 'V', 'Ac', 'E', 'S')  # Angle, Velocity, Acceleration, Energy, Scalar

# ── Operator Definitions ─────────────────────────────────────────────────────
# Each op: (name, arity, input_types, output_type, function)
OPS = []

# Unary temporal
OPS.append(('ddt',   1, ('A',),  'V',  lambda a, fs: np.gradient(a, 1/fs)))
OPS.append(('ddt',   1, ('V',),  'Ac', lambda a, fs: np.gradient(a, 1/fs)))
OPS.append(('ddt',   1, ('E',),  'E',  lambda a, fs: np.gradient(a, 1/fs)))

# Unary trig (only on angles — type enforced)
OPS.append(('sin',   1, ('A',),  'S',  lambda a, fs: np.sin(a)))
OPS.append(('cos',   1, ('A',),  'S',  lambda a, fs: np.cos(a)))

# Unary shape
OPS.append(('abs',   1, ('A',),  'A',  lambda a, fs: np.abs(a)))
OPS.append(('abs',   1, ('V',),  'V',  lambda a, fs: np.abs(a)))
OPS.append(('abs',   1, ('E',),  'E',  lambda a, fs: np.abs(a)))
OPS.append(('neg',   1, ('A',),  'A',  lambda a, fs: -a))
OPS.append(('neg',   1, ('V',),  'V',  lambda a, fs: -a))
OPS.append(('neg',   1, ('S',),  'S',  lambda a, fs: -a))
OPS.append(('square',1, ('V',),  'E',  lambda a, fs: a**2))     # ω² → energy
OPS.append(('square',1, ('A',),  'S',  lambda a, fs: a**2))     # θ² → scalar
OPS.append(('square',1, ('S',),  'S',  lambda a, fs: a**2))

# Unary rolling (temporal aggregation)
def _rstd(a, fs, w=40):
    import pandas as pd
    return pd.Series(a).rolling(w, min_periods=w//2).std().fillna(0).values

OPS.append(('rstd',  1, ('A',),  'A',  lambda a, fs: _rstd(a, fs)))
OPS.append(('rstd',  1, ('V',),  'V',  lambda a, fs: _rstd(a, fs)))
OPS.append(('rstd',  1, ('E',),  'E',  lambda a, fs: _rstd(a, fs)))

# Binary same-type (add, sub)
for t in TYPES:
    OPS.append(('add', 2, (t, t), t, lambda a, b, fs: a + b))
    OPS.append(('sub', 2, (t, t), t, lambda a, b, fs: a - b))

# Binary products (typed)
OPS.append(('mul', 2, ('V', 'V'), 'E', lambda a, b, fs: a * b))     # ω·ω → energy
OPS.append(('mul', 2, ('A', 'V'), 'E', lambda a, b, fs: a * b))     # θ·ω → energy-like
OPS.append(('mul', 2, ('S', 'A'), 'A', lambda a, b, fs: a * b))     # scalar × angle
OPS.append(('mul', 2, ('S', 'V'), 'V', lambda a, b, fs: a * b))     # scalar × vel
OPS.append(('mul', 2, ('S', 'E'), 'E', lambda a, b, fs: a * b))     # scalar × energy
OPS.append(('mul', 2, ('S', 'S'), 'S', lambda a, b, fs: a * b))

# Binary ratios (typed)
OPS.append(('div', 2, ('A', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('V', 'A'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'E'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'A'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('E', 'V'), 'S', lambda a, b, fs: a / (b + 1e-8)))
OPS.append(('div', 2, ('S', 'S'), 'S', lambda a, b, fs: a / (b + 1e-8)))

# Build lookup tables
UNARY_OPS  = {t: [(o[0], o[4], o[3]) for o in OPS if o[1]==1 and o[2]==(t,)]
              for t in TYPES}
BINARY_OPS = {}
for t1 in TYPES:
    for t2 in TYPES:
        key = (t1, t2)
        BINARY_OPS[key] = [(o[0], o[4], o[3]) for o in OPS if o[1]==2 and o[2]==key]


# ── Expression Tree ──────────────────────────────────────────────────────────
class Node:
    """A node in a typed expression tree."""
    __slots__ = ('kind', 'name', 'out_type', 'func', 'children', 'const_val')

    def __init__(self, kind, name, out_type, func=None, children=None, const_val=None):
        self.kind = kind          # 'terminal', 'const', 'unary', 'binary'
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

    def evaluate(self, signals, fs):
        """Evaluate the tree on a dict of signals {'theta': arr, 'omega': arr}."""
        try:
            if self.kind == 'terminal':
                return signals[self.name].copy()
            elif self.kind == 'const':
                return np.full(len(signals['theta']), self.const_val)
            elif self.kind == 'unary':
                child_val = self.children[0].evaluate(signals, fs)
                return self.func(child_val, fs)
            elif self.kind == 'binary':
                left  = self.children[0].evaluate(signals, fs)
                right = self.children[1].evaluate(signals, fs)
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except Exception:
            return None

    def expr_str(self):
        """Human-readable expression string."""
        if self.kind == 'terminal':
            return self.name
        elif self.kind == 'const':
            return f"{self.const_val:.2f}"
        elif self.kind == 'unary':
            return f"{self.name}({self.children[0].expr_str()})"
        elif self.kind == 'binary':
            return f"({self.children[0].expr_str()} {self.name} {self.children[1].expr_str()})"

    def copy(self):
        return deepcopy(self)


# ── Tree Generation ──────────────────────────────────────────────────────────
TERMINALS = {
    'theta': 'A',   # angle
    'omega': 'V',   # angular velocity
}
CONSTS = [0.5, 1.0, 2.0, 9.81]  # allowed constants (scalar type)

def random_terminal(target_type=None):
    """Generate a random terminal node matching target_type."""
    options = []
    for name, typ in TERMINALS.items():
        if target_type is None or typ == target_type:
            options.append(Node('terminal', name, typ))
    if target_type is None or target_type == 'S':
        c = random.choice(CONSTS)
        options.append(Node('const', f'c{c}', 'S', const_val=c))
    if not options:
        # Fallback: return any terminal
        name = random.choice(list(TERMINALS.keys()))
        return Node('terminal', name, TERMINALS[name])
    return random.choice(options)


def random_tree(max_depth, target_type=None):
    """Generate a random typed expression tree."""
    if max_depth <= 0:
        return random_terminal(target_type)

    # 30% chance of terminal even if depth allows more
    if random.random() < 0.3:
        t = random_terminal(target_type)
        if target_type is None or t.out_type == target_type:
            return t

    # Try unary operator
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

    # Try binary operator
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

    # Fallback to terminal
    return random_terminal(target_type)


# ── Tree Manipulation ────────────────────────────────────────────────────────
def all_nodes(tree, path=None):
    """Yield (node, path, parent, child_index) for every node in the tree."""
    if path is None:
        path = []
    yield tree, path, None, None
    for i, child in enumerate(tree.children):
        yield from _all_nodes_inner(child, path + [i], tree, i)

def _all_nodes_inner(node, path, parent, idx):
    yield node, path, parent, idx
    for i, child in enumerate(node.children):
        yield from _all_nodes_inner(child, path + [i], node, i)


def mutate(tree, max_depth=4):
    """Replace a random subtree with a new random subtree of same output type."""
    tree = tree.copy()
    nodes = list(_all_nodes_inner(tree, [], None, None))
    if not nodes:
        return tree
    node, path, parent, idx = random.choice(nodes)
    new_subtree = random_tree(max(1, max_depth - len(path)), node.out_type)
    if parent is None:
        return new_subtree
    parent.children[idx] = new_subtree
    return tree


def crossover(t1, t2, max_depth=4):
    """Swap type-compatible subtrees between two trees."""
    t1, t2 = t1.copy(), t2.copy()
    nodes1 = list(_all_nodes_inner(t1, [], None, None))
    nodes2 = list(_all_nodes_inner(t2, [], None, None))
    random.shuffle(nodes1)
    for n1, p1, par1, idx1 in nodes1:
        # Find compatible node in t2
        compatible = [(n2, p2, par2, idx2) for n2, p2, par2, idx2 in nodes2
                      if n2.out_type == n1.out_type and len(p2) + n1.depth() <= max_depth]
        if compatible:
            n2, p2, par2, idx2 = random.choice(compatible)
            # Swap
            if par1 is not None and par2 is not None:
                par1.children[idx1] = n2
                par2.children[idx2] = n1
                return t1, t2
    return t1, t2


# ── Fitness (Unsupervised Composite — healthy data only) ─────────────────────
DEPTH_PENALTY = 0.05

def evaluate_feature(tree, trials, fs):
    """Evaluate a tree on multiple trials, return scalar per trial."""
    scalars = []
    for run in trials:
        signals = {'theta': run['theta'], 'omega': run['omega']}
        result = tree.evaluate(signals, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 50:
            return None
        scalars.append(np.std(finite))  # summary: std of output signal
    return np.array(scalars)


def composite_fitness(tree, healthy_trials, fs):
    """Score a feature tree using ONLY healthy data. Returns 0-1 score."""
    vals = evaluate_feature(tree, healthy_trials, fs)
    if vals is None or len(vals) < 15:
        return 0.0
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0

    # Tightness: low entropy = tight distribution
    c, _ = np.histogram(vals, bins=min(20, len(vals)//3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)

    # Stationarity: stable across chunks
    chunks = np.array_split(vals, 5)
    chunk_means = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(chunk_means) < 2 or abs(np.mean(chunk_means)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(chunk_means) /
                          (abs(np.mean(chunk_means)) + 1e-8))

    # Headroom: low |kurtosis| = room for fault outliers
    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))

    # Variance gate: if it doesn't vary, it can't diagnose
    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.01:
        return 0.0

    base = (tightness + stationarity + headroom) / 3.0
    depth = tree.depth()
    return max(0.0, base - DEPTH_PENALTY * depth)


# ── GP Evolution Loop ────────────────────────────────────────────────────────
def run_gp(healthy_trials, fs,
           pop_size=300, generations=40, max_depth=4,
           tournament_k=5, elite_frac=0.05,
           crossover_rate=0.7, mutation_rate=0.25, verbose=True):
    """
    Evolve feature expressions from scratch using only healthy data.
    Returns sorted list of (fitness, tree) tuples.
    """
    # Initialize population
    population = [random_tree(max_depth) for _ in range(pop_size)]

    # Score initial population
    scores = [composite_fitness(t, healthy_trials, fs) for t in population]
    n_elite = max(2, int(pop_size * elite_frac))

    best_ever = (0.0, None)

    for gen in range(generations):
        # Sort by fitness
        paired = sorted(zip(scores, population), key=lambda x: -x[0])

        if paired[0][0] > best_ever[0]:
            best_ever = (paired[0][0], paired[0][1].copy())

        if verbose and gen % 5 == 0:
            top5 = [(s, t.expr_str()[:60]) for s, t in paired[:5]]
            print(f"  Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"median={paired[pop_size//2][0]:.4f}  "
                  f"top: {top5[0][1]}")

        # Elitism
        new_pop = [t.copy() for _, t in paired[:n_elite]]
        new_scores = [s for s, _ in paired[:n_elite]]

        # Fill rest via tournament selection + crossover/mutation
        while len(new_pop) < pop_size:
            # Tournament selection
            def tournament():
                idxs = random.sample(range(len(paired)), min(tournament_k, len(paired)))
                best_idx = max(idxs, key=lambda i: paired[i][0])
                return paired[best_idx][1]

            r = random.random()
            if r < crossover_rate:
                p1, p2 = tournament(), tournament()
                c1, c2 = crossover(p1, p2, max_depth)
                new_pop.append(c1)
                s = composite_fitness(c1, healthy_trials, fs)
                new_scores.append(s)
                if len(new_pop) < pop_size:
                    new_pop.append(c2)
                    new_scores.append(composite_fitness(c2, healthy_trials, fs))
            elif r < crossover_rate + mutation_rate:
                p = tournament()
                child = mutate(p, max_depth)
                new_pop.append(child)
                new_scores.append(composite_fitness(child, healthy_trials, fs))
            else:
                # Reproduction
                p = tournament()
                new_pop.append(p.copy())
                new_scores.append(composite_fitness(p, healthy_trials, fs))

        population = new_pop[:pop_size]
        scores = new_scores[:pop_size]

    # Final sort
    paired = sorted(zip(scores, population), key=lambda x: -x[0])
    if verbose:
        print(f"\n  Final best: {paired[0][0]:.4f} — {paired[0][1].expr_str()[:80]}")

    return paired
