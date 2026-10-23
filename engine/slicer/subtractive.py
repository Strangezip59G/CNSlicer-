from __future__ import annotations
import math
from typing import Sequence
import numpy as np
import shapely.geometry as sg
import shapely.ops as so
import trimesh

from engine.slicer.stock import StockBlock
from engine.slicer.tool import CuttingTool
from engine.slicer.toolpaths import (
    MoveType, OperationType, ToolpathPoint, ToolpathSegment, ToolpathLayer, SlicingResult
)


def simplify_coords(coords: Sequence[tuple[float, float]], tolerance: float = 0.01) -> list[tuple[float, float]]:
    """Remove redundant collinear or near-duplicate micro-movements along a 2D contour."""
    if len(coords) < 3:
        return list(coords)

    # 1. Deduplicate adjacent points closer than half tolerance
    min_dist_sq = (tolerance * 0.5) ** 2
    deduped = [coords[0]]
    for pt in coords[1:]:
        dx = pt[0] - deduped[-1][0]
        dy = pt[1] - deduped[-1][1]
        if dx * dx + dy * dy >= min_dist_sq:
            deduped.append(pt)

    if len(deduped) < 3:
        return deduped

    # 2. Filter collinear points using perpendicular distance
    simplified = [deduped[0]]
    for i in range(1, len(deduped) - 1):
        prev_p = simplified[-1]
        curr_p = deduped[i]
        next_p = deduped[i + 1]

        dx1 = curr_p[0] - prev_p[0]
        dy1 = curr_p[1] - prev_p[1]
        dx2 = next_p[0] - prev_p[0]
        dy2 = next_p[1] - prev_p[1]

        seg_len_sq = dx2 * dx2 + dy2 * dy2
        if seg_len_sq < 1e-10:
            continue

        cross = abs(dx1 * dy2 - dy1 * dx2)
        perp_dist_sq = (cross * cross) / seg_len_sq
        dot = dx1 * dx2 + dy1 * dy2

        if perp_dist_sq < (tolerance * tolerance) and (0.0 <= dot <= seg_len_sq):
            continue

        simplified.append(curr_p)

    simplified.append(deduped[-1])
    return simplified


def reconstruct_planar_polygons(
    raw_polys: Sequence[sg.Polygon],
    tolerance: float = 0.01
) -> sg.Polygon | sg.MultiPolygon:
    """Properly reconstructs 2D planar cross-sections with interior holes and islands.

    Prevents hole contours from being erased by solid unioning.
    Uses containment hierarchy to subtract interior hole cavities from their parent boundaries.
    """
    if not raw_polys:
        return sg.Polygon()

    valid_polys = [p for p in raw_polys if p.is_valid and p.area > 1e-4]
    if not valid_polys:
        return sg.Polygon()

    # Sort descending by area so enclosing outer shells are processed before their inner holes
    sorted_polys = sorted(valid_polys, key=lambda p: p.area, reverse=True)

    result_polys: list[sg.Polygon | sg.MultiPolygon] = []
    for poly in sorted_polys:
        pt = poly.representative_point()
        placed = False
        for idx, parent in enumerate(result_polys):
            if parent.contains(pt):
                # poly is an inner cavity / hole inside parent -> subtract it!
                diff = parent.difference(poly)
                if diff.is_valid and not diff.is_empty:
                    result_polys[idx] = diff
                placed = True
                break
        if not placed:
            result_polys.append(poly)

    combined = so.unary_union(result_polys) if result_polys else sg.Polygon()
    if not combined.is_empty:
        combined = combined.simplify(tolerance, preserve_topology=True)
    return combined


class SubtractiveSlicer:
    """The Subtractive Slicing Engine.

    Calculates Material to Remove = Stock(Z) - Part(Z) on every discrete Z-pass layer,
    and generates multi-pass clearing and finishing toolpaths with optional LiDAR warp compensation.
    """

    def __init__(
        self,
        stock: StockBlock,
        tool: CuttingTool,
        safe_z_margin: float = 5.0,
        rapid_speed: float = 3000.0,
        use_conformal_lidar: bool = True,
        simplification_tolerance: float = 0.01
    ):
        self.stock = stock
        self.tool = tool
        self.safe_z = stock.top_z + safe_z_margin
        self.rapid_speed = rapid_speed
        self.use_conformal_lidar = use_conformal_lidar
        self.simplification_tolerance = simplification_tolerance

    def _apply_conformal_z(self, x: float, y: float, nominal_z: float) -> float:
        """Modulate nominal Z with LiDAR surface heightmap if available."""
        if self.use_conformal_lidar and self.stock.heightmap is not None:
            return self.stock.heightmap.conformal_z(x, y, nominal_z)
        return nominal_z

    def slice_3d_mesh(
        self,
        mesh: trimesh.Trimesh,
        z_bottom: float | None = None,
        custom_stepdown: float | None = None,
        clearing_pattern: str = "concentric",
        stock_to_leave_mm: float = 0.0,
        tabs_count: int = 0,
        tab_width_mm: float = 5.0,
        tab_height_mm: float = 2.0
    ) -> SlicingResult:
        """Subtractive slice of a 3D mesh inside the stock block (e.g. stepped pyramid, relief).

        Calculates Material to Remove = Stock(Z) - Mesh(Z) at each layer pass.
        Supports both concentric and zigzag clearing, roughing stock allowance, and holding tabs.
        """
        stepdown = custom_stepdown or self.tool.stepdown_mm
        top_z = self.stock.top_z
        bottom_z = z_bottom if z_bottom is not None else (self.stock.spoilboard_z)

        # Discrete Z passes from top down to bottom
        depths = []
        curr_z = top_z - stepdown
        while curr_z >= bottom_z - 0.001:
            depths.append(round(curr_z, 4))
            curr_z -= stepdown

        # Ensure final bottom pass is included
        if not depths or abs(depths[-1] - bottom_z) > 0.05:
            depths.append(round(bottom_z, 4))

        result = SlicingResult(
            stock_width=self.stock.width_x,
            stock_length=self.stock.length_y,
            stock_thickness=self.stock.thickness_z,
            tool_diameter=self.tool.diameter_mm,
            safe_z=self.safe_z,
            rapid_feedrate=self.rapid_speed
        )

        stock_poly = self.stock.polygon

        for idx, z in enumerate(depths):
            layer = ToolpathLayer(layer_index=idx, nominal_z=z)

            # 1. Slice 3D mesh at plane Z = z (preserving global machine XY coordinates)
            slice_section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])

            if slice_section is not None and len(slice_section.vertices) > 0:
                # Use section 3D vertices projected onto XY directly.
                # (Avoid slice_section.to_2D() as to_2D() resets coordinates to local 0,0!)
                verts_2d = slice_section.vertices[:, :2]
                part_polys = []
                for entity in slice_section.entities:
                    disc = entity.discrete(verts_2d)
                    if len(disc) >= 3:
                        poly = sg.Polygon(disc)
                        if poly.is_valid and poly.area > 0.01:
                            poly = poly.simplify(self.simplification_tolerance, preserve_topology=True)
                            part_polys.append(poly)

                part_union = reconstruct_planar_polygons(part_polys, self.simplification_tolerance)
            else:
                part_union = sg.Polygon()

            # 2. Material to Remove = Stock(Z) - Part(Z) (with stock_to_leave allowance)
            if not part_union.is_empty:
                if stock_to_leave_mm > 0:
                    buffered_part = part_union.buffer(
                        stock_to_leave_mm, join_style=sg.JOIN_STYLE.mitre, mitre_limit=2.0
                    ).simplify(self.simplification_tolerance, preserve_topology=True)
                    removal_area = stock_poly.difference(buffered_part)
                else:
                    removal_area = stock_poly.difference(part_union)
            else:
                removal_area = stock_poly

            if not removal_area.is_empty:
                removal_area = removal_area.simplify(self.simplification_tolerance, preserve_topology=True)

            # 3. Generate pocket clearing passes (Concentric or Zigzag)
            if clearing_pattern == "zigzag":
                clearing_segments = self._generate_zigzag_clearing(removal_area, z)
            else:
                clearing_segments = self._generate_pocket_clearing(removal_area, z)
            layer.segments.extend(clearing_segments)

            # 4. Generate profile finishing pass around the Part perimeter
            if not part_union.is_empty:
                is_bottom_pass = (idx == len(depths) - 1)
                profile_segments = self._generate_profile_contour(
                    part_union, z, offset_outside=True,
                    tabs_count=tabs_count if is_bottom_pass else 0,
                    tab_width_mm=tab_width_mm, tab_height_mm=tab_height_mm
                )
                layer.segments.extend(profile_segments)

            result.layers.append(layer)

        return result

    def slice_25d_pockets_and_profiles(
        self,
        pockets: Sequence[sg.Polygon],
        holes: Sequence[sg.Polygon],
        perimeter: sg.Polygon | None,
        target_depth_mm: float,
        custom_stepdown: float | None = None
    ) -> SlicingResult:
        """Subtractive slice of 2.5D CAD / vector features (e.g.

        Sample 1 in drawing:
        rectangular block with through-holes/slots and perimeter cutout).
        """
        stepdown = custom_stepdown or self.tool.stepdown_mm
        top_z = self.stock.top_z
        bottom_z = top_z - target_depth_mm

        depths = []
        curr_z = top_z - stepdown
        while curr_z >= bottom_z - 0.001:
            depths.append(round(curr_z, 4))
            curr_z -= stepdown

        if not depths or abs(depths[-1] - bottom_z) > 0.05:
            depths.append(round(bottom_z, 4))

        result = SlicingResult(
            stock_width=self.stock.width_x,
            stock_length=self.stock.length_y,
            stock_thickness=self.stock.thickness_z,
            tool_diameter=self.tool.diameter_mm,
            safe_z=self.safe_z,
            rapid_feedrate=self.rapid_speed
        )

        all_cavities = list(pockets) + list(holes)

        for idx, z in enumerate(depths):
            layer = ToolpathLayer(layer_index=idx, nominal_z=z)

            # Clear all interior pockets and through-holes
            for cavity in all_cavities:
                if cavity.is_valid and cavity.area > 0.01:
                    segs = self._generate_pocket_clearing(cavity, z)
                    layer.segments.extend(segs)

            # Profile cutout around perimeter if requested
            if perimeter is not None and perimeter.is_valid and perimeter.area > 0.01:
                segs = self._generate_profile_contour(perimeter, z, offset_outside=True)
                layer.segments.extend(segs)

            result.layers.append(layer)

        return result

    def _generate_pocket_clearing(
        self,
        region: sg.Polygon | sg.MultiPolygon,
        nominal_z: float
    ) -> list[ToolpathSegment]:
        """Generate concentric offset clearing passes to hollow out a material removal region."""
        segments = []
        tool_r = self.tool.radius_mm
        stepover = self.tool.stepover_mm
        tol = self.simplification_tolerance

        # Inward offset so cutter edge stays inside the region boundary (mitre join + simplification)
        curr_offset = -tool_r
        active_area = region.buffer(curr_offset, join_style=sg.JOIN_STYLE.mitre, mitre_limit=2.0).simplify(tol, preserve_topology=True)

        # Repeated concentric offsets inward until cavity is cleared
        while not active_area.is_empty and active_area.area > (tool_r * tool_r * 0.1):
            polys = [active_area] if isinstance(active_area, sg.Polygon) else active_area.geoms

            for poly in polys:
                if poly.is_empty or len(poly.exterior.coords) < 3:
                    continue

                raw_coords = list(poly.exterior.coords)
                coords = simplify_coords(raw_coords, tolerance=tol)
                if len(coords) < 3:
                    continue

                seg = ToolpathSegment(operation=OperationType.POCKET, is_closed=True)

                # Entry move: Rapid to safe Z above start, plunge, then cut loop
                start_x, start_y = coords[0]
                entry_z = self._apply_conformal_z(start_x, start_y, nominal_z)

                seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=self.safe_z, move_type=MoveType.RAPID, feedrate_mm_min=self.rapid_speed))
                seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=entry_z, move_type=MoveType.PLUNGE, feedrate_mm_min=self.tool.plunge_rate_z_mm_min))

                for x, y in coords[1:]:
                    pt_z = self._apply_conformal_z(x, y, nominal_z)
                    seg.points.append(ToolpathPoint(x=x, y=y, z=pt_z, move_type=MoveType.CUT, feedrate_mm_min=self.tool.feedrate_xy_mm_min))

                # Retract back up to safe Z
                seg.points.append(ToolpathPoint(x=coords[-1][0], y=coords[-1][1], z=self.safe_z, move_type=MoveType.RETRACT, feedrate_mm_min=self.rapid_speed))
                segments.append(seg)

            # Step inward for next clearing ring (mitre join + simplification)
            active_area = active_area.buffer(-stepover, join_style=sg.JOIN_STYLE.mitre, mitre_limit=2.0).simplify(tol, preserve_topology=True)

        return segments

    def _generate_zigzag_clearing(
        self,
        region: sg.Polygon | sg.MultiPolygon,
        nominal_z: float
    ) -> list[ToolpathSegment]:
        """Generate parallel raster (zigzag) infill passes to clear material."""
        segments = []
        tool_r = self.tool.radius_mm
        stepover = self.tool.stepover_mm
        tol = self.simplification_tolerance

        # Inward offset so tool boundary remains strictly inside the removal region
        active_area = region.buffer(-tool_r, join_style=sg.JOIN_STYLE.mitre, mitre_limit=2.0).simplify(tol, preserve_topology=True)
        if active_area.is_empty:
            return segments

        bounds = active_area.bounds  # (minx, miny, maxx, maxy)
        y_min, y_max = bounds[1], bounds[3]
        x_min, x_max = bounds[0] - 10.0, bounds[2] + 10.0

        y_coords = np.arange(y_min + stepover * 0.5, y_max, stepover)
        if len(y_coords) == 0:
            return segments

        direction = 1  # 1 = left to right, -1 = right to left

        for y in y_coords:
            scan_line = sg.LineString([(x_min, y), (x_max, y)])
            inter = scan_line.intersection(active_area)

            if inter.is_empty:
                continue

            lines = [inter] if isinstance(inter, sg.LineString) else [g for g in getattr(inter, 'geoms', []) if isinstance(g, sg.LineString)]

            for line in lines:
                raw_coords = list(line.coords)
                coords = simplify_coords(raw_coords, tolerance=tol)
                if len(coords) < 2:
                    continue

                if direction == -1:
                    coords = coords[::-1]

                seg = ToolpathSegment(operation=OperationType.POCKET, is_closed=False)
                start_x, start_y = coords[0]
                entry_z = self._apply_conformal_z(start_x, start_y, nominal_z)

                seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=self.safe_z, move_type=MoveType.RAPID, feedrate_mm_min=self.rapid_speed))
                seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=entry_z, move_type=MoveType.PLUNGE, feedrate_mm_min=self.tool.plunge_rate_z_mm_min))

                for x, y_pt in coords[1:]:
                    pt_z = self._apply_conformal_z(x, y_pt, nominal_z)
                    seg.points.append(ToolpathPoint(x=x, y=y_pt, z=pt_z, move_type=MoveType.CUT, feedrate_mm_min=self.tool.feedrate_xy_mm_min))

                seg.points.append(ToolpathPoint(x=coords[-1][0], y=coords[-1][1], z=self.safe_z, move_type=MoveType.RETRACT, feedrate_mm_min=self.rapid_speed))
                segments.append(seg)
                direction *= -1

        return segments

    def _generate_profile_contour(
        self,
        polygon: sg.Polygon | sg.MultiPolygon,
        nominal_z: float,
        offset_outside: bool = True,
        tabs_count: int = 0,
        tab_width_mm: float = 5.0,
        tab_height_mm: float = 2.0
    ) -> list[ToolpathSegment]:
        """Generate outer/inner profile contour pass with cutter radius compensation and holding tabs."""
        segments = []
        tool_r = self.tool.radius_mm
        offset_dist = tool_r if offset_outside else -tool_r
        tol = self.simplification_tolerance
        contour = polygon.buffer(offset_dist, join_style=sg.JOIN_STYLE.mitre, mitre_limit=2.0).simplify(tol, preserve_topology=True)

        if contour.is_empty:
            return segments

        polys = [contour] if isinstance(contour, sg.Polygon) else contour.geoms

        for poly in polys:
            if poly.is_empty:
                continue

            # A. Exterior boundary profile pass (with optional holding tabs)
            if len(poly.exterior.coords) >= 3:
                raw_coords = list(poly.exterior.coords)
                coords = simplify_coords(raw_coords, tolerance=tol)
                if len(coords) >= 3:
                    seg = ToolpathSegment(operation=OperationType.PROFILE, is_closed=True)
                    start_x, start_y = coords[0]
                    entry_z = self._apply_conformal_z(start_x, start_y, nominal_z)

                    seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=self.safe_z, move_type=MoveType.RAPID, feedrate_mm_min=self.rapid_speed))
                    seg.points.append(ToolpathPoint(x=start_x, y=start_y, z=entry_z, move_type=MoveType.PLUNGE, feedrate_mm_min=self.tool.plunge_rate_z_mm_min))

                    total_pts = len(coords)
                    tab_step = (total_pts // tabs_count) if tabs_count > 0 else 999999

                    for idx_pt, (x, y) in enumerate(coords[1:]):
                        base_z = nominal_z
                        # If on a holding tab, bridge up
                        if tabs_count > 0 and (idx_pt % tab_step) < max(2, int(total_pts * 0.05)):
                            base_z = min(self.stock.top_z, nominal_z + tab_height_mm)

                        pt_z = self._apply_conformal_z(x, y, base_z)
                        seg.points.append(ToolpathPoint(x=x, y=y, z=pt_z, move_type=MoveType.CUT, feedrate_mm_min=self.tool.feedrate_xy_mm_min))

                    seg.points.append(ToolpathPoint(x=coords[-1][0], y=coords[-1][1], z=self.safe_z, move_type=MoveType.RETRACT, feedrate_mm_min=self.rapid_speed))
                    segments.append(seg)

            # B. Interior holes and cavity wall finish passes (no tabs needed inside holes)
            for interior in poly.interiors:
                if len(interior.coords) < 3:
                    continue
                raw_hole_coords = list(interior.coords)
                hole_coords = simplify_coords(raw_hole_coords, tolerance=tol)
                if len(hole_coords) < 3:
                    continue

                hole_seg = ToolpathSegment(operation=OperationType.PROFILE, is_closed=True)
                h_start_x, h_start_y = hole_coords[0]
                h_entry_z = self._apply_conformal_z(h_start_x, h_start_y, nominal_z)

                hole_seg.points.append(ToolpathPoint(x=h_start_x, y=h_start_y, z=self.safe_z, move_type=MoveType.RAPID, feedrate_mm_min=self.rapid_speed))
                hole_seg.points.append(ToolpathPoint(x=h_start_x, y=h_start_y, z=h_entry_z, move_type=MoveType.PLUNGE, feedrate_mm_min=self.tool.plunge_rate_z_mm_min))

                for x, y in hole_coords[1:]:
                    pt_z = self._apply_conformal_z(x, y, nominal_z)
                    hole_seg.points.append(ToolpathPoint(x=x, y=y, z=pt_z, move_type=MoveType.CUT, feedrate_mm_min=self.tool.feedrate_xy_mm_min))

                hole_seg.points.append(ToolpathPoint(x=hole_coords[-1][0], y=hole_coords[-1][1], z=self.safe_z, move_type=MoveType.RETRACT, feedrate_mm_min=self.rapid_speed))
                segments.append(hole_seg)

        return segments
