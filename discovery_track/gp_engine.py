"""
KnightRider Discovery Track — Type-Safe Genetic Programming Engine
==================================================================
COMPATIBILITY SHIM. The engine now lives in the domain-agnostic `typed_gp`
package; this module binds the **mechanical** TypeSystem (Angle/Velocity/Accel/
Energy/Scalar — pendulum & bearing domains) and re-exports the original
module-level API so the paper's runs (`run_discovery_v5.py`, `run_robustness.py`)
import it unchanged.

Verified byte-for-byte identical to the pre-refactor engine via
`discovery_track/_regression_check.py`. New work should import `typed_gp`
directly and pass an explicit TypeSystem (see typed_gp/typesystems.py).
"""
import functools

from typed_gp import core
from typed_gp.core import Node, crossover, all_nodes, _rstd, _rmean, DEPTH_PENALTY
from typed_gp.typesystems import mechanical_ts

# The mechanical type system — what this module historically hardcoded.
_TS = mechanical_ts()

# Re-export the legacy module-level data tables (some callers read TYPES / _rstd).
TYPES = _TS.types
TERMINALS = _TS.terminals
CONSTS = _TS.consts
OPS = _TS.ops
UNARY_OPS = _TS.unary_ops
BINARY_OPS = _TS.binary_ops

# Bind the mechanical TypeSystem so the legacy signatures (no `ts` arg) hold:
#   random_tree(max_depth, target_type=None), mutate(tree, max_depth=4), etc.
random_terminal = functools.partial(core.random_terminal, _TS)
random_tree = functools.partial(core.random_tree, _TS)
mutate = functools.partial(core.mutate, _TS)
evaluate_feature = functools.partial(core.evaluate_feature, _TS)
composite_fitness = functools.partial(core.composite_fitness, _TS)
run_gp = functools.partial(core.run_gp, _TS)
