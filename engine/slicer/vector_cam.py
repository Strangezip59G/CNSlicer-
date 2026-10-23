from __future__ import annotations
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence
import xml.etree.ElementTree as ET

import numpy as np
import shapely.geometry as sg
import shapely.affinity as sa
import shapely.ops as so
import pyclipper
import cv2

from engine.slicer.tool import CuttingTool
from engine.slicer.toolpaths import (
    MoveType, OperationType, ToolpathPoint, ToolpathSegment, ToolpathLayer, SlicingResult
)


class VectorOpMode(Enum):
    ENGRAVE = "engrave"                # Direct centerline cut (no tool offset)
    PROFILE_OUTSIDE = "profile_outside"# Cut on outside perimeter of shape (+R tool offset)
    PROFILE_INSIDE = "profile_inside"  # Cut on inside perimeter / hole cutout (-R tool offset)
    POCKET = "pocket"                  # Clear interior cavity with concentric offsets
    V_CARVE = "v_carve"                # Centerline carving for V-bit cutters


@dataclass
class HoldingTabsConfig:
    enabled: bool = False
    count: int = 4              # Number of tabs along closed outer profile
    width_mm: float = 4.0       # Tab bridge length along cut path
    height_mm: float = 1.5      # Tab bridge height left uncut at bottom


@dataclass
class VectorEntity:
    id: str
    geometry: sg.base.BaseGeometry
    is_closed: bool = True
    operation: VectorOpMode = VectorOpMode.PROFILE_OUTSIDE
    name: str = ""
    color: str = "#38bdf8"

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.geometry.bounds  # (minx, miny, maxx, maxy)

    def to_geojson_coords(self) -> list:
        """Convert geometry to nested coordinate lists for WebGL and 2D canvas."""
        if self.geometry.is_empty:
            return []
        if isinstance(self.geometry, sg.Polygon):
            coords = [list(self.geometry.exterior.coords)]
            for interior in self.geometry.interiors:
                coords.append(list(interior.coords))
            return coords
        elif isinstance(self.geometry, sg.MultiPolygon):
            all_poly_coords = []
            for poly in self.geometry.geoms:
                p_coords = [list(poly.exterior.coords)]
                for interior in poly.interiors:
                    p_coords.append(list(interior.coords))
                all_poly_coords.append(p_coords)
            return all_poly_coords
        elif isinstance(self.geometry, (sg.LineString, sg.LinearRing)):
            return [list(self.geometry.coords)]
        elif isinstance(self.geometry, sg.MultiLineString):
            return [list(ls.coords) for ls in self.geometry.geoms]
        return []


@dataclass
class VectorProject:
    entities: list[VectorEntity] = field(default_factory=list)
    source_type: str = "svg"  # "svg", "dxf", "raster_trace"
    source_filename: str = ""
    original_bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        if not self.entities:
            return (0.0, 0.0, 0.0, 0.0)
        minx = min(e.bounds[0] for e in self.entities)
        miny = min(e.bounds[1] for e in self.entities)
        maxx = max(e.bounds[2] for e in self.entities)
        maxy = max(e.bounds[3] for e in self.entities)
        return (minx, miny, maxx, maxy)

    @property
    def width(self) -> float:
        b = self.bounds
        return max(0.0, b[2] - b[0])

    @property
    def height(self) -> float:
        b = self.bounds
        return max(0.0, b[3] - b[1])

    def center_at(self, target_x: float, target_y: float) -> None:
        """Shift all entities so bounding box center is at (target_x, target_y)."""
        b = self.bounds
        if b[2] <= b[0] and b[3] <= b[1]:
            return
        cx = (b[0] + b[2]) / 2.0
        cy = (b[1] + b[3]) / 2.0
        dx = target_x - cx
        dy = target_y - cy
        for e in self.entities:
            e.geometry = sa.translate(e.geometry, xoff=dx, yoff=dy)

    def scale_to_dimensions(self, target_width: float, target_height: float, keep_aspect: bool = True) -> None:
        """Scale all entities to target dimensions in mm."""
        w, h = self.width, self.height
        if w < 1e-4 or h < 1e-4:
            return
        sx = target_width / w if target_width > 0 else 1.0
        sy = target_height / h if target_height > 0 else 1.0
        if keep_aspect:
            scale = min(sx, sy) if (target_width > 0 and target_height > 0) else (sx if target_width > 0 else sy)
            sx, sy = scale, scale

        b = self.bounds
        cx = (b[0] + b[2]) / 2.0
        cy = (b[1] + b[3]) / 2.0
        for e in self.entities:
            e.geometry = sa.scale(e.geometry, xfact=sx, yfact=sy, origin=(cx, cy))

    def to_dict(self) -> dict:
        b = self.bounds
        return {
            "source_type": self.source_type,
            "source_filename": self.source_filename,
            "bounds": {"min_x": b[0], "min_y": b[1], "max_x": b[2], "max_y": b[3]},
            "width": self.width,
            "height": self.height,
            "entity_count": len(self.entities),
            "entities": [
                {
                    "id": e.id,
                    "name": e.name,
                    "is_closed": e.is_closed,
                    "operation": e.operation.value,
                    "color": e.color,
                    "bounds": {"min_x": e.bounds[0], "min_y": e.bounds[1], "max_x": e.bounds[2], "max_y": e.bounds[3]},
                    "coords": e.to_geojson_coords()
                }
                for e in self.entities
            ]
        }


# =====================================================================
# Pure-Python SVG Path and Primitive Geometry Parser
# =====================================================================

class SVGParser:
    """Robust SVG geometry parser converting standard 2D vector tags into Shapely geometry.

    Supports:
      - Path tags (<path d="...">) with M, L, H, V, C, S, Q, T, A, Z commands
      - Shapes: <rect>, <circle>, <ellipse>, <line>, <polyline>, <polygon>
      - Standard units: mm, cm, in, pt, px
      - SVG Transforms: translate, scale, rotate, matrix
      - Y-inversion to CNC workpiece Cartesian coordinate system (Y-up)
    """

    UNIT_CONVERSIONS = {
        "mm": 1.0,
        "cm": 10.0,
        "in": 25.4,
        "pt": 25.4 / 72.0,
        "pc": 25.4 / 6.0,
        "px": 25.4 / 96.0  # CSS standard 96 DPI
    }

    @classmethod
    def parse_length(cls, val_str: str | None, has_viewbox: bool = False, default_dpi: float = 96.0) -> float:
        if not val_str:
            return 0.0
        val_str = val_str.strip().lower()
        for unit, factor in cls.UNIT_CONVERSIONS.items():
            if val_str.endswith(unit):
                try:
                    num = float(val_str[:-len(unit)].strip())
                    return num * factor
                except ValueError:
                    return 0.0
        try:
            num = float(val_str)
            # If inside a viewBox, raw numbers are in user coordinates (1.0 ratio)
            return num if has_viewbox else (num * (25.4 / default_dpi))
        except ValueError:
            return 0.0

    @classmethod
    def parse_svg_string(cls, svg_text: str, default_width_mm: float = 100.0, assemble_holes: bool = True) -> VectorProject:
        """Parse raw SVG string content into a VectorProject."""
        root = ET.fromstring(svg_text)
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        # Extract canvas viewport dimensions
        view_box = root.attrib.get("viewBox", "")
        has_vb = bool(view_box)
        vb_w, vb_h = 0.0, 0.0
        if has_vb:
            vb_parts = [float(p) for p in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", view_box)]
            if len(vb_parts) == 4:
                vb_w, vb_h = vb_parts[2], vb_parts[3]

        svg_w_str = root.attrib.get("width")
        svg_h_str = root.attrib.get("height")
        width_mm = cls.parse_length(svg_w_str, has_viewbox=False) if svg_w_str else (vb_w if vb_w > 0 else default_width_mm)
        height_mm = cls.parse_length(svg_h_str, has_viewbox=False) if svg_h_str else (vb_h if vb_h > 0 else default_width_mm)

        scale_factor = 1.0
        if has_vb and vb_w > 0 and width_mm > 0:
            scale_factor = width_mm / vb_w

        entities: list[VectorEntity] = []
        entity_counter = 0

        def traverse(element: ET.Element, current_matrix: np.ndarray):
            nonlocal entity_counter
            local_matrix = current_matrix.copy()
            transform_str = element.attrib.get("transform", "")
            if transform_str:
                t_mat = cls._parse_transform(transform_str)
                local_matrix = local_matrix @ t_mat

            tag = element.tag.lower()
            geom = None
            is_closed = True

            if tag == "path":
                d_str = element.attrib.get("d", "")
                if d_str:
                    paths = cls._parse_path_d(d_str)
                    for path_pts, closed in paths:
                        if len(path_pts) >= 2:
                            pts_mm = cls._apply_transform_to_points(path_pts, local_matrix, scale_factor)
                            if closed and len(pts_mm) >= 3:
                                geom = sg.Polygon(pts_mm)
                                if not geom.is_valid:
                                    geom = geom.buffer(0)
                                is_closed = True
                            else:
                                geom = sg.LineString(pts_mm)
                                is_closed = False

                            if geom and not geom.is_empty:
                                entity_counter += 1
                                entities.append(VectorEntity(
                                    id=f"path_{entity_counter}",
                                    geometry=geom,
                                    is_closed=is_closed,
                                    operation=VectorOpMode.PROFILE_OUTSIDE if is_closed else VectorOpMode.ENGRAVE,
                                    name=element.attrib.get("id", f"Path {entity_counter}")
                                ))

            elif tag == "rect":
                x = cls.parse_length(element.attrib.get("x", "0"), has_viewbox=has_vb)
                y = cls.parse_length(element.attrib.get("y", "0"), has_viewbox=has_vb)
                w = cls.parse_length(element.attrib.get("width", "0"), has_viewbox=has_vb)
                h = cls.parse_length(element.attrib.get("height", "0"), has_viewbox=has_vb)
                if w > 0 and h > 0:
                    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
                    pts_mm = cls._apply_transform_to_points(pts, local_matrix, scale_factor)
                    geom = sg.Polygon(pts_mm)
                    entity_counter += 1
                    entities.append(VectorEntity(
                        id=f"rect_{entity_counter}",
                        geometry=geom,
                        is_closed=True,
                        operation=VectorOpMode.PROFILE_OUTSIDE,
                        name=element.attrib.get("id", f"Rectangle {entity_counter}")
                    ))

            elif tag in ("circle", "ellipse"):
                cx = cls.parse_length(element.attrib.get("cx", "0"), has_viewbox=has_vb)
                cy = cls.parse_length(element.attrib.get("cy", "0"), has_viewbox=has_vb)
                if tag == "circle":
                    r = cls.parse_length(element.attrib.get("r", "0"), has_viewbox=has_vb)
                    rx, ry = r, r
                else:
                    rx = cls.parse_length(element.attrib.get("rx", "0"), has_viewbox=has_vb)
                    ry = cls.parse_length(element.attrib.get("ry", "0"), has_viewbox=has_vb)

                if rx > 0 and ry > 0:
                    theta = np.linspace(0, 2 * np.pi, 64)
                    pts = [(cx + rx * np.cos(t), cy + ry * np.sin(t)) for t in theta]
                    pts_mm = cls._apply_transform_to_points(pts, local_matrix, scale_factor)
                    geom = sg.Polygon(pts_mm)
                    entity_counter += 1
                    entities.append(VectorEntity(
                        id=f"{tag}_{entity_counter}",
                        geometry=geom,
                        is_closed=True,
                        operation=VectorOpMode.PROFILE_OUTSIDE,
                        name=element.attrib.get("id", f"{tag.capitalize()} {entity_counter}")
                    ))

            elif tag in ("polyline", "polygon"):
                pts_str = element.attrib.get("points", "")
                coords = [float(p) for p in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", pts_str)]
                if len(coords) >= 4:
                    pts = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
                    pts_mm = cls._apply_transform_to_points(pts, local_matrix, scale_factor)
                    if tag == "polygon" and len(pts_mm) >= 3:
                        geom = sg.Polygon(pts_mm)
                        is_closed = True
                    else:
                        geom = sg.LineString(pts_mm)
                        is_closed = False
                    entity_counter += 1
                    entities.append(VectorEntity(
                        id=f"{tag}_{entity_counter}",
                        geometry=geom,
                        is_closed=is_closed,
                        operation=VectorOpMode.PROFILE_OUTSIDE if is_closed else VectorOpMode.ENGRAVE,
                        name=element.attrib.get("id", f"{tag.capitalize()} {entity_counter}")
                    ))

            elif tag == "line":
                x1 = cls.parse_length(element.attrib.get("x1", "0"), has_viewbox=has_vb)
                y1 = cls.parse_length(element.attrib.get("y1", "0"), has_viewbox=has_vb)
                x2 = cls.parse_length(element.attrib.get("x2", "0"), has_viewbox=has_vb)
                y2 = cls.parse_length(element.attrib.get("y2", "0"), has_viewbox=has_vb)
                pts_mm = cls._apply_transform_to_points([(x1, y1), (x2, y2)], local_matrix, scale_factor)
                geom = sg.LineString(pts_mm)
                entity_counter += 1
                entities.append(VectorEntity(
                    id=f"line_{entity_counter}",
                    geometry=geom,
                    is_closed=False,
                    operation=VectorOpMode.ENGRAVE,
                    name=element.attrib.get("id", f"Line {entity_counter}")
                ))

            for child in element:
                traverse(child, local_matrix)

        identity_3x3 = np.eye(3, dtype=float)
        traverse(root, identity_3x3)

        if entities:
            max_y = max(e.bounds[3] for e in entities)
            for e in entities:
                e.geometry = sa.scale(e.geometry, yfact=-1.0, origin=(0, 0))
                e.geometry = sa.translate(e.geometry, yoff=max_y)

        if assemble_holes:
            entities = cls._assemble_hole_containment(entities)

        return VectorProject(
            entities=entities,
            source_type="svg"
        )

    @classmethod
    def _assemble_hole_containment(cls, entities: list[VectorEntity]) -> list[VectorEntity]:
        """Combine closed contours that are strictly contained within larger closed shapes as holes."""
        closed_polys = [e for e in entities if e.is_closed and isinstance(e.geometry, sg.Polygon)]
        open_entities = [e for e in entities if not e.is_closed or not isinstance(e.geometry, sg.Polygon)]

        if len(closed_polys) <= 1:
            return entities

        sorted_polys = sorted(closed_polys, key=lambda e: e.geometry.area, reverse=True)
        final_entities: list[VectorEntity] = []

        used = set()
        for i, outer_e in enumerate(sorted_polys):
            if i in used:
                continue
            outer_poly = outer_e.geometry
            holes = []
            for j in range(i + 1, len(sorted_polys)):
                if j in used:
                    continue
                inner_e = sorted_polys[j]
                inner_poly = inner_e.geometry
                if outer_poly.contains(inner_poly.representative_point()):
                    holes.append(inner_poly)
                    used.add(j)

            if holes:
                combined_outer = outer_poly
                for h in holes:
                    diff = combined_outer.difference(h)
                    if diff.is_valid and not diff.is_empty:
                        combined_outer = diff
                outer_e.geometry = combined_outer

            final_entities.append(outer_e)
            used.add(i)

        return final_entities + open_entities

    @classmethod
    def _parse_transform(cls, t_str: str) -> np.ndarray:
        mat = np.eye(3, dtype=float)
        transforms = re.findall(r"([a-zA-Z]+)\s*\(([^)]+)\)", t_str)
        for t_type, args_str in transforms:
            nums = [float(p) for p in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", args_str)]
            t_type = t_type.lower()
            if t_type == "translate" and nums:
                tx = nums[0]
                ty = nums[1] if len(nums) > 1 else 0.0
                t_mat = np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], dtype=float)
                mat = mat @ t_mat
            elif t_type == "scale" and nums:
                sx = nums[0]
                sy = nums[1] if len(nums) > 1 else sx
                s_mat = np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], dtype=float)
                mat = mat @ s_mat
            elif t_type == "rotate" and nums:
                deg = nums[0]
                rad = math.radians(deg)
                cx = nums[1] if len(nums) > 2 else 0.0
                cy = nums[2] if len(nums) > 2 else 0.0
                r_mat = np.array([
                    [math.cos(rad), -math.sin(rad), cx * (1 - math.cos(rad)) + cy * math.sin(rad)],
                    [math.sin(rad),  math.cos(rad), cy * (1 - math.cos(rad)) - cx * math.sin(rad)],
                    [0, 0, 1]
                ], dtype=float)
                mat = mat @ r_mat
            elif t_type == "matrix" and len(nums) >= 6:
                a, b, c, d, e, f = nums[:6]
                m_mat = np.array([[a, c, e], [b, d, f], [0, 0, 1]], dtype=float)
                mat = mat @ m_mat
        return mat

    @classmethod
    def _apply_transform_to_points(cls, pts: Sequence[tuple[float, float]], mat: np.ndarray, scale: float) -> list[tuple[float, float]]:
        result = []
        for x, y in pts:
            vec = np.array([x, y, 1.0], dtype=float)
            t_vec = mat @ vec
            result.append((float(t_vec[0] * scale), float(t_vec[1] * scale)))
        return result

    @classmethod
    def _parse_path_d(cls, d_str: str) -> list[tuple[list[tuple[float, float]], bool]]:
        commands = re.findall(r"([a-df-zA-DF-Z])|([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", d_str)
        tokens = []
        for cmd, num in commands:
            if cmd:
                tokens.append(cmd)
            elif num:
                tokens.append(float(num))

        subpaths: list[tuple[list[tuple[float, float]], bool]] = []
        current_pts: list[tuple[float, float]] = []
        cx, cy = 0.0, 0.0
        start_x, start_y = 0.0, 0.0
        last_control_x, last_control_y = cx, cy
        idx = 0
        current_cmd = ""

        while idx < len(tokens):
            tok = tokens[idx]
            if isinstance(tok, str):
                current_cmd = tok
                idx += 1
            elif not current_cmd:
                idx += 1
                continue

            cmd_upper = current_cmd.upper()
            is_relative = current_cmd.islower()

            if cmd_upper == "M":
                if idx + 1 < len(tokens) and isinstance(tokens[idx], (int, float)) and isinstance(tokens[idx + 1], (int, float)):
                    nx = (cx + tokens[idx]) if is_relative else tokens[idx]
                    ny = (cy + tokens[idx + 1]) if is_relative else tokens[idx + 1]
                    idx += 2
                    if current_pts:
                        subpaths.append((current_pts, False))
                    current_pts = [(nx, ny)]
                    cx, cy = nx, ny
                    start_x, start_y = cx, cy
                    last_control_x, last_control_y = cx, cy
                    current_cmd = "l" if is_relative else "L"
                else:
                    idx += 1

            elif cmd_upper == "L":
                if idx + 1 < len(tokens) and isinstance(tokens[idx], (int, float)) and isinstance(tokens[idx + 1], (int, float)):
                    nx = (cx + tokens[idx]) if is_relative else tokens[idx]
                    ny = (cy + tokens[idx + 1]) if is_relative else tokens[idx + 1]
                    idx += 2
                    current_pts.append((nx, ny))
                    cx, cy = nx, ny
                    last_control_x, last_control_y = cx, cy
                else:
                    idx += 1

            elif cmd_upper == "H":
                if idx < len(tokens) and isinstance(tokens[idx], (int, float)):
                    nx = (cx + tokens[idx]) if is_relative else tokens[idx]
                    idx += 1
                    current_pts.append((nx, cy))
                    cx = nx
                    last_control_x, last_control_y = cx, cy
                else:
                    idx += 1

            elif cmd_upper == "V":
                if idx < len(tokens) and isinstance(tokens[idx], (int, float)):
                    ny = (cy + tokens[idx]) if is_relative else tokens[idx]
                    idx += 1
                    current_pts.append((cx, ny))
                    cy = ny
                    last_control_x, last_control_y = cx, cy
                else:
                    idx += 1

            elif cmd_upper == "C":
                if idx + 5 < len(tokens):
                    x1 = (cx + tokens[idx]) if is_relative else tokens[idx]
                    y1 = (cy + tokens[idx + 1]) if is_relative else tokens[idx + 1]
                    x2 = (cx + tokens[idx + 2]) if is_relative else tokens[idx + 2]
                    y2 = (cy + tokens[idx + 3]) if is_relative else tokens[idx + 3]
                    x = (cx + tokens[idx + 4]) if is_relative else tokens[idx + 4]
                    y = (cy + tokens[idx + 5]) if is_relative else tokens[idx + 5]
                    idx += 6

                    for step in range(1, 17):
                        t = step / 16.0
                        bx = (1 - t)**3 * cx + 3 * (1 - t)**2 * t * x1 + 3 * (1 - t) * t**2 * x2 + t**3 * x
                        by = (1 - t)**3 * cy + 3 * (1 - t)**2 * t * y1 + 3 * (1 - t) * t**2 * y2 + t**3 * y
                        current_pts.append((bx, by))

                    last_control_x, last_control_y = x2, y2
                    cx, cy = x, y
                else:
                    idx += 1

            elif cmd_upper == "S":
                if idx + 3 < len(tokens):
                    x1 = 2 * cx - last_control_x
                    y1 = 2 * cy - last_control_y
                    x2 = (cx + tokens[idx]) if is_relative else tokens[idx]
                    y2 = (cy + tokens[idx + 1]) if is_relative else tokens[idx + 1]
                    x = (cx + tokens[idx + 2]) if is_relative else tokens[idx + 2]
                    y = (cy + tokens[idx + 3]) if is_relative else tokens[idx + 3]
                    idx += 4

                    for step in range(1, 17):
                        t = step / 16.0
                        bx = (1 - t)**3 * cx + 3 * (1 - t)**2 * t * x1 + 3 * (1 - t) * t**2 * x2 + t**3 * x
                        by = (1 - t)**3 * cy + 3 * (1 - t)**2 * t * y1 + 3 * (1 - t) * t**2 * y2 + t**3 * y
                        current_pts.append((bx, by))

                    last_control_x, last_control_y = x2, y2
                    cx, cy = x, y
                else:
                    idx += 1

            elif cmd_upper == "Q":
                if idx + 3 < len(tokens):
                    x1 = (cx + tokens[idx]) if is_relative else tokens[idx]
                    y1 = (cy + tokens[idx + 1]) if is_relative else tokens[idx + 1]
                    x = (cx + tokens[idx + 2]) if is_relative else tokens[idx + 2]
                    y = (cy + tokens[idx + 3]) if is_relative else tokens[idx + 3]
                    idx += 4

                    for step in range(1, 13):
                        t = step / 12.0
                        bx = (1 - t)**2 * cx + 2 * (1 - t) * t * x1 + t**2 * x
                        by = (1 - t)**2 * cy + 2 * (1 - t) * t * y1 + t**2 * y
                        current_pts.append((bx, by))

                    last_control_x, last_control_y = x1, y1
                    cx, cy = x, y
                else:
                    idx += 1

            elif cmd_upper == "A":
                if idx + 6 < len(tokens):
                    rx = abs(tokens[idx])
                    ry = abs(tokens[idx + 1])
                    rot_deg = tokens[idx + 2]
                    large_arc = bool(tokens[idx + 3])
                    sweep = bool(tokens[idx + 4])
                    target_x = (cx + tokens[idx + 5]) if is_relative else tokens[idx + 5]
                    target_y = (cy + tokens[idx + 6]) if is_relative else tokens[idx + 6]
                    idx += 7

                    arc_pts = cls._approximate_svg_arc(cx, cy, rx, ry, rot_deg, large_arc, sweep, target_x, target_y)
                    current_pts.extend(arc_pts)
                    cx, cy = target_x, target_y
                    last_control_x, last_control_y = cx, cy
                else:
                    idx += 1

            elif cmd_upper == "Z":
                if current_pts:
                    current_pts.append((start_x, start_y))
                    subpaths.append((current_pts, True))
                    current_pts = []
                cx, cy = start_x, start_y
                last_control_x, last_control_y = cx, cy
                idx += 1
            else:
                idx += 1

        if current_pts:
            subpaths.append((current_pts, False))

        return subpaths

    @classmethod
    def _approximate_svg_arc(
        cls, x1: float, y1: float, rx: float, ry: float,
        phi_deg: float, large_arc: bool, sweep: bool, x2: float, y2: float
    ) -> list[tuple[float, float]]:
        if rx < 1e-6 or ry < 1e-6 or (abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6):
            return [(x2, y2)]

        phi = math.radians(phi_deg % 360.0)
        cos_phi, sin_phi = math.cos(phi), math.sin(phi)

        dx = (x1 - x2) / 2.0
        dy = (y1 - y2) / 2.0
        x1_prime = cos_phi * dx + sin_phi * dy
        y1_prime = -sin_phi * dx + cos_phi * dy

        lam = (x1_prime**2) / (rx**2) + (y1_prime**2) / (ry**2)
        if lam > 1.0:
            rx *= math.sqrt(lam)
            ry *= math.sqrt(lam)

        sign = -1.0 if large_arc == sweep else 1.0
        num = max(0.0, (rx**2 * ry**2) - (rx**2 * y1_prime**2) - (ry**2 * x1_prime**2))
        den = (rx**2 * y1_prime**2) + (ry**2 * x1_prime**2)
        factor = sign * math.sqrt(num / den) if den > 0 else 0.0

        cx_prime = factor * (rx * y1_prime / ry)
        cy_prime = factor * (-ry * x1_prime / rx)

        cx = cos_phi * cx_prime - sin_phi * cy_prime + (x1 + x2) / 2.0
        cy = sin_phi * cx_prime + cos_phi * cy_prime + (y1 + y2) / 2.0

        def vector_angle(u: tuple[float, float], v: tuple[float, float]) -> float:
            dot = u[0] * v[0] + u[1] * v[1]
            det = u[0] * v[1] - u[1] * v[0]
            return math.atan2(det, dot)

        vx1 = (x1_prime - cx_prime) / rx
        vy1 = (y1_prime - cy_prime) / ry
        theta1 = vector_angle((1.0, 0.0), (vx1, vy1))

        vx2 = (-x1_prime - cx_prime) / rx
        vy2 = (-y1_prime - cy_prime) / ry
        d_theta = vector_angle((vx1, vy1), (vx2, vy2))

        if not sweep and d_theta > 0:
            d_theta -= 2 * math.pi
        elif sweep and d_theta < 0:
            d_theta += 2 * math.pi

        num_segments = max(4, int(math.ceil(abs(d_theta) / (math.pi / 8.0))))
        pts = []
        for i in range(1, num_segments + 1):
            theta = theta1 + (i / num_segments) * d_theta
            ex = rx * math.cos(theta)
            ey = ry * math.sin(theta)
            px = cos_phi * ex - sin_phi * ey + cx
            py = sin_phi * ex + cos_phi * ey + cy
            pts.append((px, py))

        return pts


# =====================================================================
# Pure-Python ASCII DXF Geometry Parser
# =====================================================================

class DXFParser:
    """Minimal, self-contained ASCII DXF parser for 2D CNC engineering drawings."""

    @classmethod
    def parse_dxf_text(cls, dxf_text: str, assemble_holes: bool = True) -> VectorProject:
        lines = dxf_text.splitlines()
        entities: list[VectorEntity] = []
        entity_counter = 0

        i = 0
        n = len(lines)
        in_entities_section = False

        while i < n - 1:
            code = lines[i].strip()
            val = lines[i + 1].strip()
            i += 2

            if code == "0" and val == "SECTION":
                if i < n - 1 and lines[i].strip() == "2" and lines[i + 1].strip() == "ENTITIES":
                    in_entities_section = True
                    i += 2
            elif code == "0" and val == "ENDSEC":
                in_entities_section = False
            elif in_entities_section and code == "0":
                entity_type = val.upper()

                if entity_type == "LINE":
                    p1 = [0.0, 0.0]
                    p2 = [0.0, 0.0]
                    while i < n - 1 and lines[i].strip() != "0":
                        c, v = lines[i].strip(), lines[i + 1].strip()
                        i += 2
                        if c == "10": p1[0] = float(v)
                        elif c == "20": p1[1] = float(v)
                        elif c == "11": p2[0] = float(v)
                        elif c == "21": p2[1] = float(v)
                    geom = sg.LineString([p1, p2])
                    entity_counter += 1
                    entities.append(VectorEntity(
                        id=f"dxf_line_{entity_counter}",
                        geometry=geom,
                        is_closed=False,
                        operation=VectorOpMode.ENGRAVE,
                        name=f"DXF Line {entity_counter}"
                    ))

                elif entity_type == "CIRCLE":
                    cx, cy, r = 0.0, 0.0, 0.0
                    while i < n - 1 and lines[i].strip() != "0":
                        c, v = lines[i].strip(), lines[i + 1].strip()
                        i += 2
                        if c == "10": cx = float(v)
                        elif c == "20": cy = float(v)
                        elif c == "40": r = float(v)
                    if r > 0:
                        theta = np.linspace(0, 2 * np.pi, 64)
                        pts = [(cx + r * np.cos(t), cy + r * np.sin(t)) for t in theta]
                        geom = sg.Polygon(pts)
                        entity_counter += 1
                        entities.append(VectorEntity(
                            id=f"dxf_circle_{entity_counter}",
                            geometry=geom,
                            is_closed=True,
                            operation=VectorOpMode.PROFILE_OUTSIDE,
                            name=f"DXF Circle {entity_counter}"
                        ))

                elif entity_type == "ARC":
                    cx, cy, r, a1, a2 = 0.0, 0.0, 0.0, 0.0, 0.0
                    while i < n - 1 and lines[i].strip() != "0":
                        c, v = lines[i].strip(), lines[i + 1].strip()
                        i += 2
                        if c == "10": cx = float(v)
                        elif c == "20": cy = float(v)
                        elif c == "40": r = float(v)
                        elif c == "50": a1 = float(v)
                        elif c == "51": a2 = float(v)
                    if r > 0:
                        if a2 < a1:
                            a2 += 360.0
                        rads = np.linspace(math.radians(a1), math.radians(a2), 32)
                        pts = [(cx + r * math.cos(t), cy + r * math.sin(t)) for t in rads]
                        geom = sg.LineString(pts)
                        entity_counter += 1
                        entities.append(VectorEntity(
                            id=f"dxf_arc_{entity_counter}",
                            geometry=geom,
                            is_closed=False,
                            operation=VectorOpMode.ENGRAVE,
                            name=f"DXF Arc {entity_counter}"
                        ))

                elif entity_type == "LWPOLYLINE":
                    pts = []
                    is_closed = False
                    curr_pt = [0.0, 0.0]
                    while i < n - 1 and lines[i].strip() != "0":
                        c, v = lines[i].strip(), lines[i + 1].strip()
                        i += 2
                        if c == "70":
                            is_closed = bool(int(v) & 1)
                        elif c == "10":
                            curr_pt[0] = float(v)
                        elif c == "20":
                            curr_pt[1] = float(v)
                            pts.append(tuple(curr_pt))
                    if len(pts) >= 2:
                        if is_closed and len(pts) >= 3:
                            if pts[-1] != pts[0]:
                                pts.append(pts[0])
                            geom = sg.Polygon(pts)
                        else:
                            geom = sg.LineString(pts)
                        entity_counter += 1
                        entities.append(VectorEntity(
                            id=f"dxf_poly_{entity_counter}",
                            geometry=geom,
                            is_closed=is_closed,
                            operation=VectorOpMode.PROFILE_OUTSIDE if is_closed else VectorOpMode.ENGRAVE,
                            name=f"DXF Polyline {entity_counter}"
                        ))

        if assemble_holes:
            entities = SVGParser._assemble_hole_containment(entities)

        return VectorProject(
            entities=entities,
            source_type="dxf"
        )


# =====================================================================
# High-Fidelity Image-to-Vector Tracer (OpenCV + Douglas-Peucker)
# =====================================================================

class RasterTracer:
    """Converts raster images (PNG, JPG, BMP) into vectorized CNC toolpath contours."""

    @classmethod
    def trace_image_bytes(
        cls,
        image_bytes: bytes,
        threshold_val: int = 128,
        use_otsu: bool = True,
        invert: bool = False,
        denoise: int = 3,
        approx_epsilon: float = 1.0,
        min_area_px: float = 40.0,
        target_width_mm: float = 120.0
    ) -> VectorProject:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode image file. Unsupported or corrupted raster image.")

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        if denoise > 0:
            k = max(3, denoise | 1)
            gray = cv2.GaussianBlur(gray, (k, k), 0)

        # Detect light vs dark background from corner pixel luminance
        corners = [gray[0, 0], gray[0, -1], gray[-1, 0], gray[-1, -1]]
        is_light_bg = bool(float(np.mean(corners)) > 128.0)
        if invert:
            is_light_bg = not is_light_bg

        # Invert so drawing features become foreground (255) and canvas becomes background (0)
        thresh_type = cv2.THRESH_BINARY_INV if is_light_bg else cv2.THRESH_BINARY

        if use_otsu:
            _, thresh = cv2.threshold(gray, 0, 255, thresh_type + cv2.THRESH_OTSU)
        else:
            _, thresh = cv2.threshold(gray, threshold_val, 255, thresh_type)

        contours, hierarchy = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        if not contours or hierarchy is None:
            return VectorProject(source_type="raster_trace")

        hier = hierarchy[0]

        px_to_mm = target_width_mm / float(w)

        raw_polygons: list[dict] = []
        for idx, cnt in enumerate(contours):
            area = cv2.contourArea(cnt)
            if area < min_area_px:
                continue

            epsilon = approx_epsilon * (cv2.arcLength(cnt, True) * 0.002 + 0.5)
            approx = cv2.approxPolyDP(cnt, epsilon, closed=True)

            if len(approx) < 3:
                continue

            pts_mm = []
            for pt in approx.reshape(-1, 2):
                x_mm = float(pt[0]) * px_to_mm
                y_mm = (float(h) - float(pt[1])) * px_to_mm
                pts_mm.append((x_mm, y_mm))

            if len(pts_mm) >= 3:
                p = sg.Polygon(pts_mm)
                if not p.is_valid:
                    p = p.buffer(0)
                if p.is_valid and not p.is_empty and p.area > 0.01:
                    raw_polygons.append({
                        "idx": idx,
                        "parent": hier[idx][3],
                        "poly": p,
                        "area": area
                    })

        entities: list[VectorEntity] = []
        entity_counter = 0

        # Sort so largest contours are processed first
        sorted_polys = sorted(raw_polygons, key=lambda x: x["area"], reverse=True)
        handled = set()

        for item in sorted_polys:
            c_idx = item["idx"]
            if c_idx in handled:
                continue

            main_poly = item["poly"]
            holes = []

            for child_item in raw_polygons:
                if child_item["idx"] == c_idx:
                    continue
                if child_item["parent"] == c_idx:
                    holes.append(child_item["poly"])
                    handled.add(child_item["idx"])

            if holes:
                for hole in holes:
                    diff = main_poly.difference(hole)
                    if diff.is_valid and not diff.is_empty:
                        main_poly = diff

            entity_counter += 1
            entities.append(VectorEntity(
                id=f"traced_{entity_counter}",
                geometry=main_poly,
                is_closed=True,
                operation=VectorOpMode.PROFILE_OUTSIDE,
                name=f"Traced Feature {entity_counter}",
                color="#a855f7"
            ))
            handled.add(c_idx)

        return VectorProject(
            entities=entities,
            source_type="raster_trace"
        )


# =====================================================================
# Professional Vector CAM Toolpath Generator
# =====================================================================

class VectorCAMGenerator:
    """Generates production-grade multi-pass CNC toolpaths from 2D vector entities."""

    @classmethod
    def generate_toolpaths(
        cls,
        project: VectorProject,
        operation: VectorOpMode,
        tool: CuttingTool,
        target_depth_mm: float = 3.0,
        stepdown_mm: float = 1.0,
        safe_z: float = 5.0,
        tabs: HoldingTabsConfig | None = None,
        feed_xy: float = 1200.0,
        feed_z: float = 300.0
    ) -> SlicingResult:
        if tabs is None:
            tabs = HoldingTabsConfig(enabled=False)

        tool_radius = tool.radius_mm
        stepdown = max(0.1, stepdown_mm if stepdown_mm > 0 else tool.stepdown_mm)
        target_depth = abs(target_depth_mm)

        z_levels = []
        curr_z = -stepdown
        while curr_z > -target_depth:
            z_levels.append(round(curr_z, 4))
            curr_z -= stepdown
        z_levels.append(round(-target_depth, 4))

        layers: list[ToolpathLayer] = []

        for layer_idx, z in enumerate(z_levels):
            layer = ToolpathLayer(layer_index=layer_idx + 1, nominal_z=z)

            for entity in project.entities:
                geom = entity.geometry
                if geom.is_empty:
                    continue

                if operation == VectorOpMode.ENGRAVE:
                    cls._generate_engrave_passes(geom, layer, z, safe_z, feed_xy, feed_z)

                elif operation == VectorOpMode.PROFILE_OUTSIDE:
                    cls._generate_profile_passes(
                        geom, layer, z, safe_z, tool_radius, is_outside=True,
                        tabs=tabs, target_depth=target_depth, feed_xy=feed_xy, feed_z=feed_z
                    )

                elif operation == VectorOpMode.PROFILE_INSIDE:
                    cls._generate_profile_passes(
                        geom, layer, z, safe_z, tool_radius, is_outside=False,
                        tabs=tabs, target_depth=target_depth, feed_xy=feed_xy, feed_z=feed_z
                    )

                elif operation == VectorOpMode.POCKET:
                    cls._generate_pocket_passes(
                        geom, layer, z, safe_z, tool, feed_xy, feed_z
                    )

            if layer.segments:
                layers.append(layer)

        b = project.bounds
        return SlicingResult(
            layers=layers,
            stock_width=b[2] - b[0] + 20.0,
            stock_length=b[3] - b[1] + 20.0,
            stock_thickness=target_depth + 5.0,
            tool_diameter=tool.diameter_mm,
            safe_z=safe_z,
            rapid_feedrate=3000.0
        )

    @classmethod
    def _generate_engrave_passes(
        cls, geom: sg.base.BaseGeometry, layer: ToolpathLayer,
        z: float, safe_z: float, feed_xy: float, feed_z: float
    ) -> None:
        lines = []
        if isinstance(geom, (sg.LineString, sg.LinearRing)):
            lines.append((list(geom.coords), geom.is_ring))
        elif isinstance(geom, sg.MultiLineString):
            for ls in geom.geoms:
                lines.append((list(ls.coords), ls.is_ring))
        elif isinstance(geom, sg.Polygon):
            lines.append((list(geom.exterior.coords), True))
            for interior in geom.interiors:
                lines.append((list(interior.coords), True))
        elif isinstance(geom, sg.MultiPolygon):
            for p in geom.geoms:
                lines.append((list(p.exterior.coords), True))
                for interior in p.interiors:
                    lines.append((list(interior.coords), True))

        for coords, is_closed in lines:
            if len(coords) < 2:
                continue
            seg = ToolpathSegment(operation=OperationType.ENGRAVE, is_closed=is_closed)
            seg.points.append(ToolpathPoint(x=coords[0][0], y=coords[0][1], z=safe_z, move_type=MoveType.RAPID))
            seg.points.append(ToolpathPoint(x=coords[0][0], y=coords[0][1], z=z, move_type=MoveType.PLUNGE, feedrate_mm_min=feed_z))
            for pt in coords[1:]:
                seg.points.append(ToolpathPoint(x=pt[0], y=pt[1], z=z, move_type=MoveType.CUT, feedrate_mm_min=feed_xy))
            seg.points.append(ToolpathPoint(x=coords[-1][0], y=coords[-1][1], z=safe_z, move_type=MoveType.RETRACT))
            layer.segments.append(seg)

    @classmethod
    def _generate_profile_passes(
        cls, geom: sg.base.BaseGeometry, layer: ToolpathLayer,
        z: float, safe_z: float, tool_radius: float, is_outside: bool,
        tabs: HoldingTabsConfig, target_depth: float, feed_xy: float, feed_z: float
    ) -> None:
        polys = []
        if isinstance(geom, sg.Polygon):
            polys.append(geom)
        elif isinstance(geom, sg.MultiPolygon):
            polys.extend(geom.geoms)

        for poly in polys:
            if poly.is_empty:
                continue

            offset_poly = poly.buffer(tool_radius if is_outside else -tool_radius, join_style="round")
            if offset_poly.is_empty:
                continue

            contours = []
            if isinstance(offset_poly, sg.Polygon):
                contours.append(offset_poly.exterior)
            elif isinstance(offset_poly, sg.MultiPolygon):
                for p in offset_poly.geoms:
                    contours.append(p.exterior)

            for cnt in contours:
                total_len = cnt.length
                if total_len < 1.0:
                    continue

                # Check if holding tabs apply on this Z level
                tab_floor_z = round(-(target_depth - tabs.height_mm), 4)
                apply_tabs = tabs.enabled and (z < tab_floor_z) and is_outside

                seg = ToolpathSegment(operation=OperationType.PROFILE, is_closed=True)
                start_p = cnt.interpolate(0)
                seg.points.append(ToolpathPoint(x=start_p.x, y=start_p.y, z=safe_z, move_type=MoveType.RAPID))
                seg.points.append(ToolpathPoint(x=start_p.x, y=start_p.y, z=z, move_type=MoveType.PLUNGE, feedrate_mm_min=feed_z))

                if not apply_tabs:
                    # Sample contour points
                    for dist in np.linspace(0, total_len, max(16, int(total_len * 2)))[1:]:
                        pt = cnt.interpolate(dist)
                        seg.points.append(ToolpathPoint(x=pt.x, y=pt.y, z=z, move_type=MoveType.CUT, feedrate_mm_min=feed_xy))
                else:
                    # Compute tab locations along perimeter
                    num_tabs = max(1, tabs.count)
                    tab_spacing = total_len / float(num_tabs)
                    half_w = tabs.width_mm / 2.0
                    tab_intervals = []
                    for k in range(num_tabs):
                        center_d = (k + 0.5) * tab_spacing
                        tab_intervals.append((max(0.0, center_d - half_w), min(total_len, center_d + half_w)))

                    # Walk along perimeter with fine step resolution
                    step_size = 0.5  # 0.5mm precision for crisp tab bridges
                    distances = list(np.arange(0.0, total_len, step_size))
                    if distances[-1] < total_len:
                        distances.append(total_len)

                    prev_in_tab = False
                    for dist in distances[1:]:
                        in_tab = any(start_d <= dist <= end_d for start_d, end_d in tab_intervals)
                        actual_z = tab_floor_z if in_tab else z
                        pt = cnt.interpolate(dist)

                        # If transitioning into tab, step up
                        if in_tab and not prev_in_tab:
                            seg.points.append(ToolpathPoint(x=pt.x, y=pt.y, z=tab_floor_z, move_type=MoveType.CUT, feedrate_mm_min=feed_xy))
                        elif not in_tab and prev_in_tab:
                            # Plunge back down
                            seg.points.append(ToolpathPoint(x=pt.x, y=pt.y, z=z, move_type=MoveType.PLUNGE, feedrate_mm_min=feed_z))
                        else:
                            seg.points.append(ToolpathPoint(x=pt.x, y=pt.y, z=actual_z, move_type=MoveType.CUT, feedrate_mm_min=feed_xy))

                        prev_in_tab = in_tab

                seg.points.append(ToolpathPoint(x=start_p.x, y=start_p.y, z=safe_z, move_type=MoveType.RETRACT))
                layer.segments.append(seg)

    @classmethod
    def _generate_pocket_passes(
        cls, geom: sg.base.BaseGeometry, layer: ToolpathLayer,
        z: float, safe_z: float, tool: CuttingTool, feed_xy: float, feed_z: float
    ) -> None:
        polys = []
        if isinstance(geom, sg.Polygon):
            polys.append(geom)
        elif isinstance(geom, sg.MultiPolygon):
            polys.extend(geom.geoms)

        stepover_mm = max(0.5, tool.diameter_mm * tool.stepover_fraction)
        initial_offset = tool.radius_mm

        for poly in polys:
            if poly.is_empty:
                continue

            current_offset = initial_offset
            while True:
                inward_poly = poly.buffer(-current_offset, join_style="round")
                if inward_poly.is_empty:
                    break

                rings = []
                if isinstance(inward_poly, sg.Polygon):
                    rings.append(inward_poly.exterior)
                    rings.extend(inward_poly.interiors)
                elif isinstance(inward_poly, sg.MultiPolygon):
                    for p in inward_poly.geoms:
                        rings.append(p.exterior)
                        rings.extend(p.interiors)

                for ring in rings:
                    coords = list(ring.coords)
                    if len(coords) < 3:
                        continue
                    seg = ToolpathSegment(operation=OperationType.POCKET, is_closed=True)
                    seg.points.append(ToolpathPoint(x=coords[0][0], y=coords[0][1], z=safe_z, move_type=MoveType.RAPID))
                    seg.points.append(ToolpathPoint(x=coords[0][0], y=coords[0][1], z=z, move_type=MoveType.PLUNGE, feedrate_mm_min=feed_z))
                    for pt in coords[1:]:
                        seg.points.append(ToolpathPoint(x=pt[0], y=pt[1], z=z, move_type=MoveType.CUT, feedrate_mm_min=feed_xy))
                    seg.points.append(ToolpathPoint(x=coords[-1][0], y=coords[-1][1], z=safe_z, move_type=MoveType.RETRACT))
                    layer.segments.append(seg)

                current_offset += stepover_mm
