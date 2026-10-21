"""
Auto-Machining Optimizer Package for CNC Subtractive Slicing.
"""
from engine.optimizer.material_database import get_material, MATERIALS, WorkpieceMaterial
from engine.optimizer.machining_optimizer import MachiningOptimizer, OptimizerRecommendation, OptimizationMetrics
from engine.optimizer.gcode_optimizer import GCodeOptimizer, fit_arcs_to_segment, rdp_simplify_points

__all__ = [
    "get_material",
    "MATERIALS",
    "WorkpieceMaterial",
    "MachiningOptimizer",
    "OptimizerRecommendation",
    "OptimizationMetrics",
    "GCodeOptimizer",
    "fit_arcs_to_segment",
    "rdp_simplify_points",
]
