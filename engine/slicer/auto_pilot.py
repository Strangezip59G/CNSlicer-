"""
CNC Milling Slicer — Subtractive Studio
Intelligent CAM Auto-Pilot Engine
Automatically determines optimal tool selection, machining strategy, cutting parameters,
and speeds & feeds based on 3D geometry analysis and active CNC machine capabilities.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math

from engine.slicer.machine import CNCProfile, STANDARD_CNC_MACHINES
from engine.slicer.analyzer import DesignAnalysisResult


@dataclass
class AutoSetupRecommendation:
    """Comprehensive automatic machining configuration."""
    tool_key: str              # "flat_1_8", "flat_1_4", "flat_6mm", "v_bit_60"
    tool_name: str
    tool_diameter_mm: float
    clearing_pattern: str      # "concentric" or "zigzag"
    stepdown_mm: float
    stepover_pct: int
    stock_to_leave_mm: float
    tabs_count: int
    tab_width_mm: float
    tab_height_mm: float
    spindle_rpm: int
    feed_xy_mm_min: float
    feed_z_mm_min: float
    recommended_stock_shape: str
    recommended_stock_dims: dict
    summary: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tool_key": self.tool_key,
            "tool_name": self.tool_name,
            "tool_diameter_mm": self.tool_diameter_mm,
            "clearing_pattern": self.clearing_pattern,
            "stepdown_mm": self.stepdown_mm,
            "stepover_pct": self.stepover_pct,
            "stock_to_leave_mm": self.stock_to_leave_mm,
            "tabs_count": self.tabs_count,
            "tab_width_mm": self.tab_width_mm,
            "tab_height_mm": self.tab_height_mm,
            "spindle_rpm": self.spindle_rpm,
            "feed_xy_mm_min": self.feed_xy_mm_min,
            "feed_z_mm_min": self.feed_z_mm_min,
            "recommended_stock_shape": self.recommended_stock_shape,
            "recommended_stock_dims": self.recommended_stock_dims,
            "summary": self.summary,
            "reasons": self.reasons,
        }


class AutoPilot:
    """Calculates scientifically sound machining parameters matching CAD geometry to machine capabilities."""

    @classmethod
    def compute_recommendations(
        cls,
        analysis: DesignAnalysisResult,
        machine: CNCProfile
    ) -> AutoSetupRecommendation:
        reasons = []

        w_x = analysis.width_x
        l_y = analysis.length_y
        h_z = analysis.height_z
        min_span = min(w_x, l_y)
        max_span = max(w_x, l_y)
        aspect_ratio = min_span / max_span if max_span > 0 else 1.0
        vol = analysis.volume_cm3
        circ = analysis.circularity

        # ---------------------------------------------------------------------
        # 1. Optimal Tool Selection
        # ---------------------------------------------------------------------
        is_desktop_class = (machine.spindle_max_rpm <= 12000) or ("Desktop" in machine.category) or (machine.id in ["genmitsu_3018", "bantam_desktop"])
        is_heavy_router = machine.id in ["shapeoko_4", "x_carve_1000", "genmitsu_4030"]

        if h_z <= 3.5 and vol < 5.0 and h_z > 0:
            tool_key = "v_bit_60"
            tool_name = "60° V-Carve Bit"
            tool_dia = 6.35
            reasons.append("Selected 60° V-Bit for fine relief detailing on shallow profile.")
        elif min_span < 35.0 or h_z < 12.0 or vol < 25.0:
            tool_key = "flat_1_8"
            tool_name = '1/8" (3.175mm) Flat Endmill'
            tool_dia = 3.175
            reasons.append(f"Selected 1/8\" (3.175mm) Endmill for {min_span:.1f}mm compact geometry and tight internal corners.")
        elif is_heavy_router and vol >= 60.0:
            tool_key = "flat_6mm"
            tool_name = "6.0mm Roughing Endmill"
            tool_dia = 6.0
            reasons.append(f"Selected 6.0mm Roughing Endmill to maximize material removal on {vol:.1f} cm³ volume on {machine.name}.")
        else:
            if is_desktop_class and min_span < 65.0:
                tool_key = "flat_1_8"
                tool_name = '1/8" (3.175mm) Flat Endmill'
                tool_dia = 3.175
                reasons.append(f"Selected 1/8\" (3.175mm) Endmill to avoid spindle bog-down on {machine.name}.")
            else:
                tool_key = "flat_1_4"
                tool_name = '1/4" (6.35mm) Flat Endmill'
                tool_dia = 6.35
                reasons.append('Selected 1/4" (6.35mm) Endmill for rigid, balanced pocket clearing.')

        # Ensure tool fits into any detected internal holes or top cavities
        min_hole_dia = getattr(analysis, "min_hole_diameter_mm", 0.0)
        if min_hole_dia > 0 and tool_dia >= min_hole_dia:
            tool_key = "flat_1_8"
            tool_name = '1/8" (3.175mm) Flat Endmill'
            tool_dia = 3.175
            reasons.append(f"Downsized to 1/8\" (3.175mm) Endmill to fit inside detected {min_hole_dia:.1f}mm internal holes.")

        # ---------------------------------------------------------------------
        # 2. Optimal Machining Strategy (Pocket Clearing Pattern)
        # ---------------------------------------------------------------------
        if circ >= 0.65 or analysis.detected_shape in ["cylinder", "round_plate"]:
            clearing_pattern = "concentric"
            reasons.append("Selected Concentric (Spiral Outward) clearing for constant chip load on circular contours.")
        elif aspect_ratio < 0.50 or analysis.detected_shape == "plate":
            clearing_pattern = "zigzag"
            reasons.append(f"Selected Zig-Zag (Parallel Raster) clearing for efficiency on elongated {w_x:.0f}×{l_y:.0f}mm geometry.")
        else:
            clearing_pattern = "concentric"
            reasons.append("Selected Concentric (Spiral Outward) clearing to minimize air travel and eliminate cutter corner chatter.")

        # ---------------------------------------------------------------------
        # 3. Cutting Parameters (Stepdown & Stepover)
        # ---------------------------------------------------------------------
        if is_desktop_class:
            # Conservative DOC for low-power desktop spindles (~100-300W)
            stepdown = round(max(0.4, min(tool_dia * 0.32, 1.2)), 1)
        else:
            # Moderate DOC for rigid benchtop mills & routers
            stepdown = round(max(0.6, min(tool_dia * 0.45, 2.5)), 1)

        if h_z > 0 and stepdown > h_z:
            stepdown = round(h_z / max(1, math.ceil(h_z / 1.0)), 1)

        if clearing_pattern == "concentric":
            stepover_pct = 45
            reasons.append(f"Stepdown set to {stepdown} mm with 45% stepover for smooth continuous spiral milling.")
        else:
            stepover_pct = 55
            reasons.append(f"Stepdown set to {stepdown} mm with 55% stepover for complete raster coverage without ridges.")

        # Finishing Allowance
        stock_to_leave = 0.2 if tool_key != "v_bit_60" else 0.0

        # Holding Tabs
        if h_z >= 10.0 and max_span > 60.0:
            tabs_count = 4
            tab_width = 5.0
            tab_height = 2.0
            reasons.append("Configured 4 holding tabs (5×2mm) to secure workpiece during final perimeter through-cut.")
        elif h_z >= 6.0:
            tabs_count = 2
            tab_width = 4.0
            tab_height = 1.5
            reasons.append("Configured 2 holding tabs (4×1.5mm) to prevent part breakout.")
        else:
            tabs_count = 0
            tab_width = 5.0
            tab_height = 2.0

        # ---------------------------------------------------------------------
        # 4. Speeds & Feeds
        # ---------------------------------------------------------------------
        max_rpm = machine.spindle_max_rpm
        min_rpm = machine.spindle_min_rpm

        # Calculate target RPM based on tool diameter
        if tool_dia <= 3.2:
            target_rpm = 18000
            chipload_mm = 0.035
        elif tool_dia <= 6.0:
            target_rpm = 15000
            chipload_mm = 0.050
        else:
            target_rpm = 14000
            chipload_mm = 0.055

        spindle_rpm = int(max(min_rpm, min(target_rpm, max_rpm)))
        reasons.append(f"Spindle speed set to {spindle_rpm:,} RPM (scaled within {machine.name}'s {max_rpm:,} RPM limit).")

        # Theoretical feed rate = RPM * 2 flutes * chipload
        computed_feed_xy = spindle_rpm * 2 * chipload_mm
        # Safe bounding within machine limits
        safe_feed_xy = max(400.0, min(computed_feed_xy, machine.default_feed_xy * 1.25, machine.rapid_speed * 0.70))
        feed_xy = round(safe_feed_xy, -1)

        # Plunge rate (typically 25% - 35% of XY feed, capped by machine plunge limit)
        computed_feed_z = feed_xy * 0.28
        safe_feed_z = max(150.0, min(computed_feed_z, machine.default_plunge_z * 1.15))
        feed_z = round(safe_feed_z, -1)

        reasons.append(f"Feedrate set to {feed_xy:.0f} mm/min (plunge {feed_z:.0f} mm/min) for optimal chipload.")

        # ---------------------------------------------------------------------
        # 5. Recommended Stock Shape & Dimensions
        # ---------------------------------------------------------------------
        stock_shape = analysis.detected_shape
        if stock_shape == "round_plate":
            stock_dims = analysis.recommended_round_plate
        elif stock_shape == "cylinder":
            stock_dims = analysis.recommended_cylinder
        elif stock_shape == "plate":
            stock_dims = analysis.recommended_plate
        else:
            stock_dims = analysis.recommended_box

        summary = f"{tool_name} • {clearing_pattern.capitalize()} • {stepdown}mm DOC • {spindle_rpm:,} RPM @ {feed_xy:.0f} mm/min"

        return AutoSetupRecommendation(
            tool_key=tool_key,
            tool_name=tool_name,
            tool_diameter_mm=tool_dia,
            clearing_pattern=clearing_pattern,
            stepdown_mm=stepdown,
            stepover_pct=stepover_pct,
            stock_to_leave_mm=stock_to_leave,
            tabs_count=tabs_count,
            tab_width_mm=tab_width,
            tab_height_mm=tab_height,
            spindle_rpm=spindle_rpm,
            feed_xy_mm_min=feed_xy,
            feed_z_mm_min=feed_z,
            recommended_stock_shape=stock_shape,
            recommended_stock_dims=stock_dims,
            summary=summary,
            reasons=reasons
        )
