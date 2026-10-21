from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import shapely.geometry as sg
import shapely.ops as so
import trimesh


@dataclass
class DesignAnalysisResult:
    """Comprehensive analysis of 3D model geometry for CNC stock sizing and toolpath meshing."""
    width_x: float
    length_y: float
    height_z: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    centroid: list[float]
    volume_cm3: float
    surface_area_cm2: float
    footprint_area_mm2: float
    perimeter_length_mm: float
    triangle_count: int
    vertex_count: int
    is_watertight: bool

    # Minimum bounding cylinder centered at (centroid_x, centroid_y)
    min_cylinder_diameter_mm: float
    min_cylinder_height_mm: float

    # Recommended stock sizes (with safety margin)
    recommended_box: dict[str, float]
    recommended_cylinder: dict[str, float]
    recommended_plate: dict[str, float]
    recommended_round_plate: dict[str, float]

    # 2D Outlines (for projection onto spoilboard bed)
    silhouette_exterior: list[list[float]] = field(default_factory=list)
    silhouette_interiors: list[list[list[float]]] = field(default_factory=list)
    convex_hull: list[list[float]] = field(default_factory=list)

    # 3D Feature edge lines for CAD/CAM wireframe highlighting
    feature_edges: list[list[list[float]]] = field(default_factory=list)


class DesignAnalyzer:
    """Analyzes 3D designs to generate precise 2D/3D outlines for stock sizing, meshing, and toolpath boundaries."""

    def __init__(self, crease_angle_deg: float = 25.0):
        self.crease_angle_deg = crease_angle_deg

    def analyze(
        self,
        mesh: trimesh.Trimesh,
        margin_xy: float = 5.0,
        margin_z_top: float = 2.0,
        margin_z_bottom: float = 0.0
    ) -> DesignAnalysisResult:
        """Run full geometric analysis on the mesh:

        - 2D silhouette boundary and holes
        - 2D convex hull
        - Sharp 3D feature edges
        - Bounding cylinder for round billet/plate turning
        - Recommended stock parameters
        """
        bounds = mesh.bounds
        extents = mesh.extents
        centroid = mesh.centroid.tolist()

        min_x, min_y, min_z = float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2])
        max_x, max_y, max_z = float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2])
        w_x, l_y, h_z = float(extents[0]), float(extents[1]), float(extents[2])

        # Volume & surface area
        try:
            vol = float(mesh.volume) / 1000.0 if mesh.is_watertight else float(mesh.convex_hull.volume) / 1000.0
        except Exception:
            vol = (w_x * l_y * h_z) / 1000.0  # fallback approximate volume in cm3

        area_cm2 = float(mesh.area) / 100.0

        # Minimum bounding cylinder around centroid in XY
        verts_xy = mesh.vertices[:, :2]
        cx, cy = centroid[0], centroid[1]
        distances = np.linalg.norm(verts_xy - np.array([cx, cy]), axis=1)
        max_radius = float(np.max(distances)) if len(distances) > 0 else (max(w_x, l_y) / 2.0)
        cyl_dia = max_radius * 2.0

        # Recommended stock with margins
        rec_box = {
            "width": round(w_x + margin_xy * 2.0, 1),
            "length": round(l_y + margin_xy * 2.0, 1),
            "thickness": round(h_z + margin_z_top + margin_z_bottom, 1)
        }
        rec_plate = {
            "width": round(w_x + margin_xy * 2.0, 1),
            "length": round(l_y + margin_xy * 2.0, 1),
            "thickness": round(max(3.0, h_z + margin_z_top), 1)
        }
        rec_cyl = {
            "diameter": round(cyl_dia + margin_xy * 2.0, 1),
            "thickness": round(h_z + margin_z_top + margin_z_bottom, 1)
        }
        rec_round_plate = {
            "diameter": round(cyl_dia + margin_xy * 2.0, 1),
            "thickness": round(max(3.0, h_z + margin_z_top), 1)
        }

        # Extract 2D silhouette projection outline
        silhouette_poly = self.extract_silhouette(mesh)
        footprint_area = float(silhouette_poly.area)
        perimeter_len = float(silhouette_poly.length)

        # Exterior and interior loops for rendering
        ext_coords = []
        int_coords = []
        if isinstance(silhouette_poly, sg.Polygon):
            ext_coords = [list(pt) for pt in silhouette_poly.exterior.coords]
            for ring in silhouette_poly.interiors:
                int_coords.append([list(pt) for pt in ring.coords])
        elif isinstance(silhouette_poly, sg.MultiPolygon):
            for poly in silhouette_poly.geoms:
                ext_coords.extend([list(pt) for pt in poly.exterior.coords])
                for ring in poly.interiors:
                    int_coords.append([list(pt) for pt in ring.coords])

        # Convex hull outline
        hull = silhouette_poly.convex_hull
        hull_coords = [list(pt) for pt in hull.exterior.coords] if isinstance(hull, sg.Polygon) else []

        # Sharp feature edges
        feature_lines = self.extract_feature_edges(mesh)

        return DesignAnalysisResult(
            width_x=round(w_x, 2),
            length_y=round(l_y, 2),
            height_z=round(h_z, 2),
            min_x=round(min_x, 2),
            max_x=round(max_x, 2),
            min_y=round(min_y, 2),
            max_y=round(max_y, 2),
            min_z=round(min_z, 2),
            max_z=round(max_z, 2),
            centroid=[round(c, 2) for c in centroid],
            volume_cm3=round(vol, 2),
            surface_area_cm2=round(area_cm2, 2),
            footprint_area_mm2=round(footprint_area, 2),
            perimeter_length_mm=round(perimeter_len, 2),
            triangle_count=len(mesh.faces),
            vertex_count=len(mesh.vertices),
            is_watertight=bool(mesh.is_watertight),
            min_cylinder_diameter_mm=round(cyl_dia, 2),
            min_cylinder_height_mm=round(h_z, 2),
            recommended_box=rec_box,
            recommended_cylinder=rec_cyl,
            recommended_plate=rec_plate,
            recommended_round_plate=rec_round_plate,
            silhouette_exterior=ext_coords,
            silhouette_interiors=int_coords,
            convex_hull=hull_coords,
            feature_edges=feature_lines
        )

    def extract_silhouette(self, mesh: trimesh.Trimesh) -> sg.Polygon | sg.MultiPolygon:
        """Project mesh triangles onto the XY plane and compute the union silhouette."""
        # Simplify mesh if huge to keep processing sub-second
        m = mesh
        if len(m.faces) > 5000:
            try:
                m = m.simplify_quadric_decimation(3000)
            except Exception:
                pass

        verts_2d = m.vertices[:, :2]
        triangles = []
        for face in m.faces:
            if len(face) == 3:
                pts = verts_2d[face]
                # Fast check that triangle is not degenerate
                tri = sg.Polygon(pts)
                if tri.is_valid and tri.area > 1e-4:
                    triangles.append(tri)

        if not triangles:
            # Fallback to 2D bounding box
            b = mesh.bounds
            return sg.box(b[0][0], b[0][1], b[1][0], b[1][1])

        merged = so.unary_union(triangles)
        if not merged.is_valid:
            merged = merged.buffer(0)
        return merged

    def extract_feature_edges(self, mesh: trimesh.Trimesh, max_edges: int = 1500) -> list[list[list[float]]]:
        """Extract sharp feature creases where dihedral angle between faces > crease_angle_deg."""
        try:
            threshold_rad = np.radians(self.crease_angle_deg)
            angles = mesh.face_adjacency_angles
            sharp_mask = angles > threshold_rad
            edge_pairs = mesh.face_adjacency_edges[sharp_mask]

            if len(edge_pairs) > max_edges:
                # Subsample evenly if mesh is very dense
                step = len(edge_pairs) // max_edges + 1
                edge_pairs = edge_pairs[::step]

            edge_coords = mesh.vertices[edge_pairs]  # shape (N, 2, 3)
            # Round coordinates to 2 decimal places for clean JSON transport
            return np.round(edge_coords, 2).tolist()
        except Exception:
            return []

    def compute_conforming_stock_outline(
        self,
        mesh: trimesh.Trimesh,
        margin_xy: float = 5.0
    ) -> sg.Polygon | sg.MultiPolygon:
        """Create a conforming stock outline polygon that snugly buffers the design silhouette."""
        silhouette = self.extract_silhouette(mesh)
        buffered = silhouette.buffer(margin_xy, join_style=sg.JOIN_STYLE.round)
        if not buffered.is_valid:
            buffered = buffered.buffer(0)
        return buffered
