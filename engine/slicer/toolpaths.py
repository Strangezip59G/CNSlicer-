from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import math


class MoveType(Enum):
    RAPID = "rapid"        # G0 (non-cutting air move)
    PLUNGE = "plunge"      # G1 (vertical feed into material)
    CUT = "cut"            # G1 (cutting move in material)
    RETRACT = "retract"    # G0 (rapid lift up to safe clearance)


class OperationType(Enum):
    FACING = "facing"        # Surface leveling top of stock
    POCKET = "pocket"        # Clearing cavity / material removal around part
    PROFILE = "profile"      # Wall contouring / cutout with tool offset
    ENGRAVE = "engrave"      # Centerline vector tracing
    DRILL = "drill"          # Direct hole plunge


@dataclass
class ToolpathPoint:
    x: float
    y: float
    z: float
    move_type: MoveType = MoveType.CUT
    feedrate_mm_min: float = 1200.0


@dataclass
class ToolpathSegment:
    """A continuous tool motion path (e.g. one pocket clearing loop or profile pass)."""
    points: list[ToolpathPoint] = field(default_factory=list)
    operation: OperationType = OperationType.PROFILE
    is_closed: bool = False

    @property
    def total_length_mm(self) -> float:
        """Calculate total 3D travel distance of this segment in mm."""
        if len(self.points) < 2:
            return 0.0
        dist = 0.0
        for i in range(len(self.points) - 1):
            p1, p2 = self.points[i], self.points[i + 1]
            dist += math.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2 + (p2.z - p1.z)**2)
        return dist


@dataclass
class ToolpathLayer:
    """Represents all machining operations at a discrete Z slicing level (pass)."""
    layer_index: int
    nominal_z: float  # The theoretical target Z depth of this pass
    segments: list[ToolpathSegment] = field(default_factory=list)

    @property
    def total_length_mm(self) -> float:
        return sum(seg.total_length_mm for seg in self.segments)


@dataclass
class SlicingResult:
    """Complete multi-layer subtractive slicing output ready for simulation and G-code export."""
    layers: list[ToolpathLayer] = field(default_factory=list)
    stock_width: float = 0.0
    stock_length: float = 0.0
    stock_thickness: float = 0.0
    tool_diameter: float = 3.175
    safe_z: float = 5.0
    rapid_feedrate: float = 3000.0

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def total_cut_length_mm(self) -> float:
        total = 0.0
        for lyr in self.layers:
            for seg in lyr.segments:
                for i in range(len(seg.points) - 1):
                    p1, p2 = seg.points[i], seg.points[i + 1]
                    if p2.move_type == MoveType.CUT:
                        total += math.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2 + (p2.z - p1.z)**2)
        return total

    @property
    def total_rapid_length_mm(self) -> float:
        total = 0.0
        for lyr in self.layers:
            for seg in lyr.segments:
                for i in range(len(seg.points) - 1):
                    p1, p2 = seg.points[i], seg.points[i + 1]
                    if p2.move_type in (MoveType.RAPID, MoveType.RETRACT):
                        total += math.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2 + (p2.z - p1.z)**2)
        return total

    @property
    def estimated_time_minutes(self) -> float:
        """Estimated machining time accounting for cut feedrates, plunge rates, rapid travel speeds, and dwell."""
        total_seconds = 2.0  # Spindle spinup dwell (G4 P2.0)
        for lyr in self.layers:
            for seg in lyr.segments:
                for i in range(len(seg.points) - 1):
                    p1, p2 = seg.points[i], seg.points[i + 1]
                    dist = math.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2 + (p2.z - p1.z)**2)
                    speed = p2.feedrate_mm_min if p2.move_type not in (MoveType.RAPID, MoveType.RETRACT) else self.rapid_feedrate
                    if speed > 0:
                        total_seconds += (dist / speed) * 60.0
        return total_seconds / 60.0
