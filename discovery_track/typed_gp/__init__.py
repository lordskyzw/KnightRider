"""Typed Genetic Programming — one engine, pluggable per-domain TypeSystems."""
from .core import (
    TypeSystem, Node,
    random_terminal, random_tree, mutate, crossover, all_nodes,
    evaluate_feature, composite_fitness, run_gp,
    _rstd, _rmean, DEPTH_PENALTY,
)
from .typesystems import mechanical_ts, obd_ts, ecg_ts, OBD_TERMINALS
from .loaders import load_knight_rider_obd, KR_SIGNAL_TO_OBD

__all__ = [
    'TypeSystem', 'Node',
    'random_terminal', 'random_tree', 'mutate', 'crossover', 'all_nodes',
    'evaluate_feature', 'composite_fitness', 'run_gp',
    '_rstd', '_rmean', 'DEPTH_PENALTY',
    'mechanical_ts', 'obd_ts', 'ecg_ts', 'OBD_TERMINALS',
    'load_knight_rider_obd', 'KR_SIGNAL_TO_OBD',
]
