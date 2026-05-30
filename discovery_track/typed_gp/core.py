"""
Typed Genetic Programming — domain-agnostic core.
=================================================
The one evolve/tree engine, parameterised by a [TypeSystem]. Physical types
prevent nonsense ops (sin(energy), angle+velocity); the *same* machinery serves
the mechanical (pendulum/bearing), automotive (OBD-II), and physiological (ECG)
domains — they differ only in their TypeSystem config (see typesystems.py).

This is a faithful extraction of the original `gp_engine.py`: every function is
structurally identical, with the hardcoded module globals (TYPES/OPS/TERMINALS/
CONSTS) and signal names lifted into the passed-in TypeSystem. The mechanical
config + this core reproduce the original byte-for-byte (see _regression_check).

NO hand-crafted features. NO fault data during evolution — fitness is scored on
healthy data only.
"""
import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy import stats


# ── Shared temporal helpers (referenced by operator tables) ──────────────────
def _rstd(a, fs, w=40):
    import pandas as pd
    return pd.Series(a).rolling(w, min_periods=w // 2).std().fillna(0).values


def _rmean(a, fs, w=40):
    import pandas as pd
    return pd.Series(a).rolling(w, min_periods=w // 2).mean().fillna(0).values


# ── Type system ──────────────────────────────────────────────────────────────
@dataclass
class TypeSystem:
    """A domain's physical types + operators + input signals.

    * ``types``     — tuple of physical type tags, e.g. ('A','V','Ac','E','S').
    * ``ops``       — list of (name, arity, input_types, output_type, func);
                      func signature is (a, fs) for unary, (a, b, fs) for binary.
    * ``terminals`` — ordered {signal_name: type} for the model's inputs.
    * ``consts``    — scalar constants available as terminals (output type 'S').

    ``unary_ops`` / ``binary_ops`` lookup tables are derived in insertion order,
    so a config that lists types/ops in the original order reproduces the exact
    RNG draw sequence of the legacy engine.
    """
    types: tuple
    ops: list
    terminals: dict
    consts: list
    unary_ops: dict = field(default_factory=dict, repr=False)
    binary_ops: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.unary_ops = {
            t: [(o[0], o[4], o[3]) for o in self.ops if o[1] == 1 and o[2] == (t,)]
            for t in self.types
        }
        self.binary_ops = {}
        for t1 in self.types:
            for t2 in self.types:
                key = (t1, t2)
                self.binary_ops[key] = [
                    (o[0], o[4], o[3]) for o in self.ops if o[1] == 2 and o[2] == key
                ]


# ── Expression tree ──────────────────────────────────────────────────────────
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
        """Evaluate the tree on a dict of signals {name: array}."""
        try:
            if self.kind == 'terminal':
                return signals[self.name].copy()
            elif self.kind == 'const':
                # length from any input signal (domain-agnostic; was 'theta')
                n = len(next(iter(signals.values())))
                return np.full(n, self.const_val)
            elif self.kind == 'unary':
                child_val = self.children[0].evaluate(signals, fs)
                return self.func(child_val, fs)
            elif self.kind == 'binary':
                left = self.children[0].evaluate(signals, fs)
                right = self.children[1].evaluate(signals, fs)
                n = min(len(left), len(right))
                return self.func(left[:n], right[:n], fs)
        except Exception:
            return None

    def expr_str(self):
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


# ── Tree generation ──────────────────────────────────────────────────────────
def random_terminal(ts: TypeSystem, target_type=None):
    """Generate a random terminal node matching target_type."""
    options = []
    for name, typ in ts.terminals.items():
        if target_type is None or typ == target_type:
            options.append(Node('terminal', name, typ))
    if target_type is None or target_type == 'S':
        c = random.choice(ts.consts)
        options.append(Node('const', f'c{c}', 'S', const_val=c))
    if not options:
        name = random.choice(list(ts.terminals.keys()))
        return Node('terminal', name, ts.terminals[name])
    return random.choice(options)


def random_tree(ts: TypeSystem, max_depth, target_type=None):
    """Generate a random typed expression tree."""
    if max_depth <= 0:
        return random_terminal(ts, target_type)

    if random.random() < 0.3:
        t = random_terminal(ts, target_type)
        if target_type is None or t.out_type == target_type:
            return t

    if random.random() < 0.5:
        candidates = []
        for in_type in ts.types:
            for name, func, out in ts.unary_ops.get(in_type, []):
                if target_type is None or out == target_type:
                    candidates.append((name, func, out, in_type))
        if candidates:
            name, func, out, in_type = random.choice(candidates)
            child = random_tree(ts, max_depth - 1, in_type)
            return Node('unary', name, out, func, [child])

    candidates = []
    for (t1, t2), ops in ts.binary_ops.items():
        for name, func, out in ops:
            if target_type is None or out == target_type:
                candidates.append((name, func, out, t1, t2))
    if candidates:
        name, func, out, t1, t2 = random.choice(candidates)
        left = random_tree(ts, max_depth - 1, t1)
        right = random_tree(ts, max_depth - 1, t2)
        return Node('binary', name, out, func, [left, right])

    return random_terminal(ts, target_type)


# ── Tree manipulation ────────────────────────────────────────────────────────
def all_nodes(tree, path=None):
    if path is None:
        path = []
    yield tree, path, None, None
    for i, child in enumerate(tree.children):
        yield from _all_nodes_inner(child, path + [i], tree, i)


def _all_nodes_inner(node, path, parent, idx):
    yield node, path, parent, idx
    for i, child in enumerate(node.children):
        yield from _all_nodes_inner(child, path + [i], node, i)


def mutate(ts: TypeSystem, tree, max_depth=4):
    """Replace a random subtree with a new random subtree of same output type."""
    tree = tree.copy()
    nodes = list(_all_nodes_inner(tree, [], None, None))
    if not nodes:
        return tree
    node, path, parent, idx = random.choice(nodes)
    new_subtree = random_tree(ts, max(1, max_depth - len(path)), node.out_type)
    if parent is None:
        return new_subtree
    parent.children[idx] = new_subtree
    return tree


def crossover(t1, t2, max_depth=4):
    """Swap type-compatible subtrees between two trees (type-system agnostic)."""
    t1, t2 = t1.copy(), t2.copy()
    nodes1 = list(_all_nodes_inner(t1, [], None, None))
    nodes2 = list(_all_nodes_inner(t2, [], None, None))
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


# ── Fitness (unsupervised composite — healthy data only) ─────────────────────
DEPTH_PENALTY = 0.05


def evaluate_feature(ts: TypeSystem, tree, trials, fs, summary=np.std):
    """Evaluate a tree on multiple trials, return a scalar per trial."""
    scalars = []
    for run in trials:
        signals = {name: run[name] for name in ts.terminals}
        result = tree.evaluate(signals, fs)
        if result is None or not np.any(np.isfinite(result)):
            return None
        finite = result[np.isfinite(result)]
        if len(finite) < 50:
            return None
        scalars.append(summary(finite))
    return np.array(scalars)


def composite_fitness(ts: TypeSystem, tree, healthy_trials, fs):
    """Score a feature tree using ONLY healthy data. Returns 0-1 score."""
    vals = evaluate_feature(ts, tree, healthy_trials, fs)
    if vals is None or len(vals) < 15:
        return 0.0
    vals = vals[np.isfinite(vals)]
    if len(vals) < 15 or np.std(vals) < 1e-10:
        return 0.0

    c, _ = np.histogram(vals, bins=min(20, len(vals) // 3))
    p = c / c.sum(); p = p[p > 0]
    H = -np.sum(p * np.log2(p))
    H_max = np.log2(len(p)) + 1e-8
    tightness = 1.0 / (1.0 + H / H_max)

    chunks = np.array_split(vals, 5)
    chunk_means = [ch.mean() for ch in chunks if len(ch) > 2]
    if len(chunk_means) < 2 or abs(np.mean(chunk_means)) < 1e-8:
        stationarity = 1.0
    else:
        stationarity = max(0.0, 1.0 - np.std(chunk_means) /
                           (abs(np.mean(chunk_means)) + 1e-8))

    kurt = float(stats.kurtosis(vals, fisher=True))
    headroom = 1.0 / (1.0 + abs(kurt))

    cv = np.std(vals) / (abs(np.mean(vals)) + 1e-8)
    if cv < 0.01:
        return 0.0

    base = (tightness + stationarity + headroom) / 3.0
    depth = tree.depth()
    return max(0.0, base - DEPTH_PENALTY * depth)


# ── GP evolution loop (simple standalone variant) ────────────────────────────
def run_gp(ts: TypeSystem, healthy_trials, fs,
           pop_size=300, generations=40, max_depth=4,
           tournament_k=5, elite_frac=0.05,
           crossover_rate=0.7, mutation_rate=0.25, verbose=True):
    """Evolve feature expressions from scratch using only healthy data.
    Returns a sorted list of (fitness, tree) tuples."""
    population = [random_tree(ts, max_depth) for _ in range(pop_size)]
    scores = [composite_fitness(ts, t, healthy_trials, fs) for t in population]
    n_elite = max(2, int(pop_size * elite_frac))
    best_ever = (0.0, None)

    for gen in range(generations):
        paired = sorted(zip(scores, population), key=lambda x: -x[0])
        if paired[0][0] > best_ever[0]:
            best_ever = (paired[0][0], paired[0][1].copy())
        if verbose and gen % 5 == 0:
            top5 = [(s, t.expr_str()[:60]) for s, t in paired[:5]]
            print(f"  Gen {gen:3d}: best={paired[0][0]:.4f}  "
                  f"median={paired[pop_size//2][0]:.4f}  top: {top5[0][1]}")

        new_pop = [t.copy() for _, t in paired[:n_elite]]
        new_scores = [s for s, _ in paired[:n_elite]]

        while len(new_pop) < pop_size:
            def tournament():
                idxs = random.sample(range(len(paired)), min(tournament_k, len(paired)))
                best_idx = max(idxs, key=lambda i: paired[i][0])
                return paired[best_idx][1]

            r = random.random()
            if r < crossover_rate:
                p1, p2 = tournament(), tournament()
                c1, c2 = crossover(p1, p2, max_depth)
                new_pop.append(c1)
                new_scores.append(composite_fitness(ts, c1, healthy_trials, fs))
                if len(new_pop) < pop_size:
                    new_pop.append(c2)
                    new_scores.append(composite_fitness(ts, c2, healthy_trials, fs))
            elif r < crossover_rate + mutation_rate:
                p = tournament()
                child = mutate(ts, p, max_depth)
                new_pop.append(child)
                new_scores.append(composite_fitness(ts, child, healthy_trials, fs))
            else:
                p = tournament()
                new_pop.append(p.copy())
                new_scores.append(composite_fitness(ts, p, healthy_trials, fs))

        population = new_pop[:pop_size]
        scores = new_scores[:pop_size]

    paired = sorted(zip(scores, population), key=lambda x: -x[0])
    if verbose:
        print(f"\n  Final best: {paired[0][0]:.4f} — {paired[0][1].expr_str()[:80]}")
    return paired
