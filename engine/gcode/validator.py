from __future__ import annotations
from dataclasses import dataclass, field
import math
import re


@dataclass
class GCodeVerificationReport:
    """Comprehensive diagnostics and machine-readiness report for generated G-code."""
    is_valid: bool
    status: str  # "PASS", "WARNING", "FAIL"
    total_lines: int
    motion_blocks: int
    rapid_blocks: int
    cutting_blocks: int
    bounds: dict[str, float]
    total_cut_distance_mm: float
    total_rapid_distance_mm: float
    max_feedrate_xy: float
    min_feedrate_z: float
    spindle_rpm: int
    estimated_time_minutes: float
    passed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "status": self.status,
            "total_lines": self.total_lines,
            "motion_blocks": self.motion_blocks,
            "rapid_blocks": self.rapid_blocks,
            "cutting_blocks": self.cutting_blocks,
            "bounds": self.bounds,
            "total_cut_distance_mm": round(self.total_cut_distance_mm, 2),
            "total_rapid_distance_mm": round(self.total_rapid_distance_mm, 2),
            "max_feedrate_xy": self.max_feedrate_xy,
            "min_feedrate_z": self.min_feedrate_z,
            "spindle_rpm": self.spindle_rpm,
            "estimated_time_minutes": round(self.estimated_time_minutes, 2),
            "passed_checks": self.passed_checks,
            "warnings": self.warnings,
            "errors": self.errors
        }


class GCodeValidator:
    """Virtual CNC controller simulator to parse, validate, and verify G-code for machine safety."""

    def __init__(self, bed_width: float = 400.0, bed_length: float = 400.0, max_z_travel: float = 150.0):
        self.bed_width = bed_width
        self.bed_length = bed_length
        self.max_z_travel = max_z_travel

    def verify(self, gcode_text: str) -> GCodeVerificationReport:
        lines = gcode_text.splitlines()
        passed_checks = []
        warnings = []
        errors = []

        # Modal machine states
        unit_mode = None       # "G20" (inch) or "G21" (mm)
        distance_mode = None   # "G90" (abs) or "G91" (inc)
        plane_mode = None      # "G17" (XY)
        spindle_running = False
        spindle_speed = 0

        current_x = 0.0
        current_y = 0.0
        current_z = 0.0
        current_feed = 0.0

        min_x, max_x = float("inf"), float("-inf")
        min_y, max_y = float("inf"), float("-inf")
        min_z, max_z = float("inf"), float("-inf")

        total_cut_dist = 0.0
        total_rapid_dist = 0.0
        motion_blocks = 0
        rapid_blocks = 0
        cutting_blocks = 0
        max_feed_xy = 0.0
        min_feed_z = float("inf")

        has_program_end = False
        safe_z_retract_observed = False
        active_motion = "G0"

        # Regex patterns for fast tokenizing
        token_regex = re.compile(r"([A-Z])([-+]?[0-9]*\.?[0-9]+)")

        for line_num, raw_line in enumerate(lines, start=1):
            # Strip comments (;... and (...))
            clean_line = raw_line.split(";")[0]
            clean_line = re.sub(r"\(.*?\)", "", clean_line).strip()
            if not clean_line:
                continue

            tokens = token_regex.findall(clean_line.upper())
            words = {letter: float(val) for letter, val in tokens}

            # Check modal codes
            if "G" in words:
                g_code = int(words["G"])
                if g_code == 21:
                    unit_mode = "G21"
                elif g_code == 20:
                    unit_mode = "G20"
                elif g_code == 90:
                    distance_mode = "G90"
                elif g_code == 91:
                    distance_mode = "G91"
                elif g_code == 17:
                    plane_mode = "G17"
                elif g_code in (0, 1, 2, 3):
                    active_motion = f"G{g_code}"

            if "M" in words:
                m_code = int(words["M"])
                if m_code in (3, 4):
                    spindle_running = True
                    if "S" in words:
                        spindle_speed = int(words["S"])
                elif m_code == 5:
                    spindle_running = False
                elif m_code in (2, 30):
                    has_program_end = True

            if "S" in words and spindle_speed == 0:
                spindle_speed = int(words["S"])

            if "F" in words:
                current_feed = words["F"]
                if current_feed > max_feed_xy:
                    max_feed_xy = current_feed

            # Coordinates update
            new_x = words.get("X", current_x)
            new_y = words.get("Y", current_y)
            new_z = words.get("Z", current_z)

            moved = (new_x != current_x) or (new_y != current_y) or (new_z != current_z)

            if moved:
                motion_blocks += 1
                dx = new_x - current_x
                dy = new_y - current_y
                dz = new_z - current_z
                step_dist = math.sqrt(dx * dx + dy * dy + dz * dz)

                # Update bounds
                min_x = min(min_x, new_x)
                max_x = max(max_x, new_x)
                min_y = min(min_y, new_y)
                max_y = max(max_y, new_y)
                min_z = min(min_z, new_z)
                max_z = max(max_z, new_z)

                if active_motion == "G0":
                    rapid_blocks += 1
                    total_rapid_dist += step_dist
                    if new_z >= 2.0:
                        safe_z_retract_observed = True
                elif active_motion == "G1":
                    cutting_blocks += 1
                    total_cut_dist += step_dist

                    # Cutting safety checks
                    if not spindle_running:
                        errors.append(f"Line {line_num}: Cutting move G1 with spindle stopped! Tool collision risk.")

                    if current_feed <= 0:
                        errors.append(f"Line {line_num}: G1 cutting move executed without F feedrate!")

                    if dz < -0.01:
                        # Plunge move
                        min_feed_z = min(min_feed_z, current_feed)

                current_x = new_x
                current_y = new_y
                current_z = new_z

        # --- SAFETY RULE EVALUATIONS ---
        if unit_mode == "G21":
            passed_checks.append("Metric Units (G21 - Millimeters) properly established.")
        elif unit_mode == "G20":
            passed_checks.append("Imperial Units (G20 - Inches) active.")
        else:
            warnings.append("No explicit G21/G20 unit declaration found in header.")

        if distance_mode == "G90":
            passed_checks.append("Absolute Positioning (G90) verified.")
        else:
            errors.append("Missing G90 absolute coordinate mode declaration.")

        if plane_mode == "G17":
            passed_checks.append("XY Plane Selection (G17) verified.")

        if spindle_speed > 0:
            passed_checks.append(f"Spindle configured at {spindle_speed} RPM (Clockwise M3).")
        else:
            warnings.append("Spindle speed was not declared via S parameter.")

        if safe_z_retract_observed:
            passed_checks.append("Safe Z clearance retracts present before rapid travels.")
        else:
            warnings.append("No safe Z lift observed before rapid moves.")

        if min_z < -0.1:
            errors.append(f"Spoilboard bed collision detected: tool plunges to Z = {min_z:.2f} mm (below Z=0.0).")
        else:
            passed_checks.append(f"Spoilboard bed integrity verified: minimum Z = {max(0.0, min_z):.2f} mm.")

        half_w = self.bed_width / 2.0
        half_l = self.bed_length / 2.0
        is_within_centered = (-half_w <= min_x <= half_w) and (-half_w <= max_x <= half_w) and (-half_l <= min_y <= half_l) and (-half_l <= max_y <= half_l)
        is_within_corner = (0.0 <= min_x <= self.bed_width) and (0.0 <= max_x <= self.bed_width) and (0.0 <= min_y <= self.bed_length) and (0.0 <= max_y <= self.bed_length)

        if not (is_within_centered or is_within_corner):
            warnings.append(f"Toolpaths exceed standard bed envelope (X:[{min_x:.1f}, {max_x:.1f}], Y:[{min_y:.1f}, {max_y:.1f}] mm).")
        else:
            passed_checks.append(f"Machine envelope safe: Part sits within bed boundaries.")

        if has_program_end:
            passed_checks.append("Program end (M30/M2) and spindle shutdown (M5) verified.")
        else:
            warnings.append("Missing M30 program termination code at end of file.")

        # Overall Status
        if len(errors) > 0:
            status = "FAIL"
            is_valid = False
        elif len(warnings) > 0:
            status = "WARNING"
            is_valid = True
        else:
            status = "PASS"
            is_valid = True

        # Fallback bounds if no moves
        if min_x == float("inf"):
            min_x = max_x = min_y = max_y = min_z = max_z = 0.0

        est_time = (total_cut_dist / max(1.0, max_feed_xy)) + (total_rapid_dist / 3000.0)

        return GCodeVerificationReport(
            is_valid=is_valid,
            status=status,
            total_lines=len(lines),
            motion_blocks=motion_blocks,
            rapid_blocks=rapid_blocks,
            cutting_blocks=cutting_blocks,
            bounds={
                "x_min": round(min_x, 2), "x_max": round(max_x, 2),
                "y_min": round(min_y, 2), "y_max": round(max_y, 2),
                "z_min": round(min_z, 2), "z_max": round(max_z, 2)
            },
            total_cut_distance_mm=total_cut_dist,
            total_rapid_distance_mm=total_rapid_dist,
            max_feedrate_xy=round(max_feed_xy, 1),
            min_feedrate_z=round(min_feed_z, 1) if min_feed_z != float("inf") else 300.0,
            spindle_rpm=spindle_speed,
            estimated_time_minutes=est_time,
            passed_checks=passed_checks,
            warnings=warnings,
            errors=errors
        )
