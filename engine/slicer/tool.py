from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class ToolType(Enum):
    FLAT_ENDMILL = "flat_endmill"
    BALL_NOSE = "ball_nose"
    V_BIT = "v_bit"
    BULL_NOSE = "bull_nose"


@dataclass
class CuttingTool:
    """Represents a CNC cutting bit in the tool library."""
    tool_id: int
    name: str
    tool_type: ToolType
    diameter_mm: float  # Cutting diameter in mm
    flute_length_mm: float = 20.0
    number_of_flutes: int = 2
    stepdown_mm: float = 1.0  # Depth of cut per pass
    stepover_fraction: float = 0.45  # Stepover as fraction of diameter (e.g. 45%)
    feedrate_xy_mm_min: float = 1200.0  # Cutting feedrate in XY plane
    plunge_rate_z_mm_min: float = 300.0  # Z axis plunge speed
    spindle_rpm: int = 16000
    v_bit_angle_deg: float | None = None  # For V-bits (e.g. 60 or 90 deg)

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm / 2.0

    @property
    def stepover_mm(self) -> float:
        return self.diameter_mm * self.stepover_fraction


# Standard default tools
DEFAULT_FLAT_1_8 = CuttingTool(
    tool_id=1,
    name='1/8" (3.175mm) 2-Flute Flat Endmill',
    tool_type=ToolType.FLAT_ENDMILL,
    diameter_mm=3.175,
    stepdown_mm=1.0,
    stepover_fraction=0.45,
    feedrate_xy_mm_min=1200.0,
    plunge_rate_z_mm_min=300.0,
    spindle_rpm=16000
)

DEFAULT_FLAT_1_4 = CuttingTool(
    tool_id=2,
    name='1/4" (6.35mm) 2-Flute Flat Endmill',
    tool_type=ToolType.FLAT_ENDMILL,
    diameter_mm=6.35,
    stepdown_mm=2.0,
    stepover_fraction=0.50,
    feedrate_xy_mm_min=1800.0,
    plunge_rate_z_mm_min=450.0,
    spindle_rpm=14000
)
