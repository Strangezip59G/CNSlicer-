from __future__ import annotations
from engine.slicer.toolpaths import SlicingResult, MoveType


class GCodeGenerator:
    """Generates clean, standardized machine G-code (GRBL, LinuxCNC, Mach3/4, Marlin)

    from SlicingResult with safe tool clearance and modal optimization.
    """

    def __init__(self, dialect: str = "grbl"):
        self.dialect = dialect.lower()

    def generate(
        self,
        result: SlicingResult,
        spindle_rpm: int = 16000,
        program_name: str = "CNC_SLICER_JOB"
    ) -> str:
        lines: list[str] = []

        # --- HEADER / SAFETY BLOCK ---
        lines.append(f"; ==========================================")
        lines.append(f"; Program: {program_name}")
        lines.append(f"; Sliced with: CNC Milling Slicer Engine")
        lines.append(f"; Layers: {result.layer_count}")
        lines.append(f"; Tool Diameter: {result.tool_diameter:.3f} mm")
        lines.append(f"; Estimated Cut Time: {result.estimated_time_minutes:.2f} min")
        lines.append(f"; Total Cut Travel: {result.total_cut_length_mm:.1f} mm")
        lines.append(f"; ==========================================")
        lines.append("G21 ; Set units to millimeters")
        lines.append("G90 ; Absolute positioning")
        lines.append("G17 ; Select XY plane")
        lines.append("G94 ; Feed rate per minute")
        lines.append(f"G0 Z{result.safe_z:.3f} ; Move to safe clearance plane")
        lines.append(f"M3 S{spindle_rpm} ; Start spindle clockwise")
        lines.append("G4 P2.0 ; Dwell 2.0s for spindle to reach full speed")
        lines.append("")

        current_feed = -1.0
        last_x, last_y, last_z = None, None, None

        # --- BODY: LAYER BY LAYER ---
        for lyr in result.layers:
            lines.append(f"; --- LAYER {lyr.layer_index + 1}/{result.layer_count}: Nominal Z = {lyr.nominal_z:.3f} mm ---")

            for seg_idx, seg in enumerate(lyr.segments):
                if not seg.points:
                    continue

                lines.append(f"; Op: {seg.operation.value.upper()} (Seg {seg_idx + 1})")

                for pt in seg.points:
                    cmd_parts = []

                    if pt.move_type == MoveType.RAPID or pt.move_type == MoveType.RETRACT:
                        cmd_parts.append("G0")
                    else:
                        cmd_parts.append("G1")

                    # Coordinate updates
                    coord_str = ""
                    if last_x is None or abs(pt.x - last_x) > 0.0005:
                        coord_str += f" X{pt.x:.3f}"
                        last_x = pt.x
                    if last_y is None or abs(pt.y - last_y) > 0.0005:
                        coord_str += f" Y{pt.y:.3f}"
                        last_y = pt.y
                    if last_z is None or abs(pt.z - last_z) > 0.0005:
                        coord_str += f" Z{pt.z:.3f}"
                        last_z = pt.z

                    if not coord_str:
                        continue  # No movement

                    cmd_parts.append(coord_str.strip())

                    # Feedrate update if changed and not rapid
                    if pt.move_type in (MoveType.CUT, MoveType.PLUNGE):
                        if abs(pt.feedrate_mm_min - current_feed) > 0.5:
                            cmd_parts.append(f"F{pt.feedrate_mm_min:.0f}")
                            current_feed = pt.feedrate_mm_min

                    lines.append(" ".join(cmd_parts))

            lines.append("")

        # --- FOOTER ---
        lines.append("; --- PROGRAM END & SAFE PARK ---")
        lines.append(f"G0 Z{result.safe_z:.3f} ; Retract to safe Z")
        lines.append("M5 ; Stop spindle")
        lines.append("G0 X0.000 Y0.000 ; Return to WCS origin")
        lines.append("M30 ; End of program")

        return "\n".join(lines) + "\n"
