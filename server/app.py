from __future__ import annotations
import io
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, request, send_file, send_from_directory
import numpy as np
import shapely.geometry as sg
import trimesh

from engine.lidar.mock_sensor import MockLidarSensor
from engine.lidar.scanner import ToolheadLidarScanner
from engine.slicer.stock import StockBlock, StockShape
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.slicer.analyzer import DesignAnalyzer
from engine.slicer.auto_pilot import AutoPilot
from engine.slicer.machine import STANDARD_CNC_MACHINES, get_machine_profile, CNCProfile, BedLayoutType
from engine.gcode.generator import GCodeGenerator
from engine.gcode.validator import GCodeValidator
from engine.optimizer.material_database import MATERIALS, get_material
from engine.optimizer.machining_optimizer import MachiningOptimizer
from engine.optimizer.gcode_optimizer import GCodeOptimizer

# Resolve UI directory robustly across source code, onefile bundles, and onedir layouts
def _find_ui_dir() -> Path:
    candidates = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "ui")
        candidates.append(Path(sys.executable).parent / "ui")
        candidates.append(Path(sys.executable).parent / "_internal" / "ui")
    candidates.append(ROOT_DIR / "ui")
    candidates.append(Path(__file__).resolve().parent.parent / "ui")
    for c in candidates:
        if c.exists() and (c / "index.html").exists():
            return c
    return ROOT_DIR / "ui"

UI_DIR = _find_ui_dir()
STATIC_DIR = UI_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(UI_DIR))

# Global state for latest sliced job and loaded 3D model
latest_slicing_result = None
latest_stock = None
latest_optimized_gcode: str | None = None
latest_optimizer_report: dict | None = None
current_loaded_mesh: trimesh.Trimesh | None = None
current_mesh_metadata: dict = {}
active_machine: CNCProfile = STANDARD_CNC_MACHINES["genmitsu_3018"]


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "-1"
    return response


@app.get("/")
def index():
    return send_file(UI_DIR / "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.get("/api/machines")
def get_machines():
    """Retrieve list of supported CNC milling machines with their bed envelopes & spindle limits."""
    return jsonify({
        "success": True,
        "active_machine_id": active_machine.id,
        "active_machine": active_machine.to_dict(),
        "machines": [m.to_dict() for m in STANDARD_CNC_MACHINES.values()]
    })


@app.post("/api/machines/select")
def select_machine():
    """Select the active milling machine or update custom bed parameters."""
    global active_machine
    data = request.get_json(force=True) or {}
    machine_id = data.get("machine_id", "genmitsu_3018")

    if machine_id == "custom_mill" and ("bed_width_x" in data or "bed_length_y" in data):
        custom = STANDARD_CNC_MACHINES["custom_mill"]
        if "bed_width_x" in data:
            custom.bed_width_x = float(data["bed_width_x"])
        if "bed_length_y" in data:
            custom.bed_length_y = float(data["bed_length_y"])
        if "max_z_travel" in data:
            custom.max_z_travel = float(data["max_z_travel"])
        if "spindle_max_rpm" in data:
            custom.spindle_max_rpm = int(data["spindle_max_rpm"])
        active_machine = custom
    else:
        active_machine = get_machine_profile(machine_id)

    return jsonify({
        "success": True,
        "active_machine": active_machine.to_dict()
    })


@app.post("/api/lidar/scan")
def scan_lidar():
    """Simulate toolhead LiDAR sweeping the bed to detect material placement & dimensions."""
    data = request.get_json(force=True) or {}
    bed_w = float(data.get("bed_w", 400.0))
    bed_l = float(data.get("bed_l", 400.0))

    stock_x = float(data.get("stock_x", 75.0))
    stock_y = float(data.get("stock_y", 65.0))
    stock_w = float(data.get("stock_w", 120.0))
    stock_l = float(data.get("stock_l", 90.0))
    stock_t = float(data.get("stock_t", 22.0))

    sensor = MockLidarSensor(bed_width=bed_w, bed_length=bed_l, spoilboard_z=0.0, noise_std_mm=0.015)
    sensor.place_virtual_stock(
        x_min=stock_x, y_min=stock_y,
        width=stock_w, length=stock_l,
        thickness=stock_t,
        tilt_x_mm=0.12, tilt_y_mm=-0.08
    )

    scan_pts = sensor.simulate_sweep(step_x=5.0, step_y=5.0)
    scanner = ToolheadLidarScanner(bed_width=bed_w, bed_length=bed_l, spoilboard_z=0.0)
    detected = scanner.process_bed_scan(scan_pts, height_threshold_mm=2.0)

    if detected is None:
        return jsonify({"detected": False, "error": "No stock material found on bed."}), 404

    global latest_stock
    latest_stock = StockBlock(
        width_x=detected.width,
        length_y=detected.length,
        thickness_z=detected.thickness,
        origin_x=detected.x_min,
        origin_y=detected.y_min,
        heightmap=detected.heightmap
    )

    return jsonify({
        "detected": True,
        "x_min": detected.x_min,
        "x_max": detected.x_max,
        "y_min": detected.y_min,
        "y_max": detected.y_max,
        "width": detected.width,
        "length": detected.length,
        "thickness": detected.thickness,
        "max_deviation": detected.heightmap.max_deviation
    })


@app.post("/api/slice")
def slice_model():
    """Execute subtractive slicing: Stock(Z) - Part(Z) down every Z layer."""
    global latest_slicing_result, latest_stock, latest_optimized_gcode, active_machine
    latest_optimized_gcode = None
    data = request.get_json(force=True) or {}

    # Machine profile selection if provided
    machine_id = data.get("machine_id")
    if machine_id:
        active_machine = get_machine_profile(machine_id)

    model_type = data.get("model_type", "sample1")
    stock_shape = str(data.get("stock_shape", "box")).lower()
    stock_w = float(data.get("stock_w", 100.0))
    stock_l = float(data.get("stock_l", 80.0))
    stock_t = float(data.get("stock_t", 20.0))
    stock_d = float(data.get("stock_d", max(stock_w, stock_l)))
    stock_lock_to_model = bool(data.get("stock_lock_to_model", True))
    stock_origin_x = float(data.get("stock_origin_x", -stock_w / 2.0))
    stock_origin_y = float(data.get("stock_origin_y", -stock_l / 2.0))

    stepdown = float(data.get("stepdown", 1.0))
    stepover = float(data.get("stepover", 0.45))
    tool_d = float(data.get("tool_d", 3.175))
    feed_xy = float(data.get("feed_xy", 1200.0))
    feed_z = float(data.get("feed_z", 300.0))

    # Transform parameters (Creality Slicer controls)
    pos_x = float(data.get("pos_x", 0.0))
    pos_y = float(data.get("pos_y", 0.0))
    pos_z = float(data.get("pos_z", 0.0))
    rot_x = float(data.get("rot_x", 0.0))
    rot_y = float(data.get("rot_y", 0.0))
    rot_z = float(data.get("rot_z", 0.0))
    scale_x = float(data.get("scale_x", 1.0))
    scale_y = float(data.get("scale_y", 1.0))
    scale_z = float(data.get("scale_z", 1.0))

    clearing_pattern = str(data.get("clearing_pattern", "concentric"))
    stock_to_leave = float(data.get("stock_to_leave", 0.0))
    tabs_count = int(data.get("tabs_count", 0))
    tab_width = float(data.get("tab_width", 5.0))
    tab_height = float(data.get("tab_height", 2.0))

    target_cx = pos_x
    target_cy = pos_y

    shape_enum = StockShape(stock_shape) if stock_shape in StockShape._value2member_map_ else StockShape.BOX
    stock = StockBlock(
        shape=shape_enum,
        width_x=stock_w,
        length_y=stock_l,
        thickness_z=stock_t,
        diameter_d=stock_d,
        origin_x=target_cx - stock_w / 2.0,
        origin_y=target_cy - stock_l / 2.0
    )
    if stock_lock_to_model:
        stock.set_center(target_cx, target_cy)

    tool = CuttingTool(
        tool_id=1,
        name=f"{tool_d:.3f}mm Endmill",
        tool_type=ToolType.FLAT_ENDMILL,
        diameter_mm=tool_d,
        stepdown_mm=stepdown,
        stepover_fraction=stepover,
        feedrate_xy_mm_min=feed_xy,
        plunge_rate_z_mm_min=feed_z
    )

    simplification_tol = float(data.get("simplification_tol", 0.01))
    slicer = SubtractiveSlicer(stock=stock, tool=tool, simplification_tolerance=simplification_tol)

    mesh_to_slice = None
    if model_type == "custom_stl" and current_loaded_mesh is not None:
        mesh_to_slice = current_loaded_mesh.copy()
    elif model_type == "sample2":
        # Sample 2: 3-Tier Stepped Pyramid from solid stock
        b1 = trimesh.creation.box(extents=[20, 20, 10])
        b1.apply_translation([0, 0, 25])
        b2 = trimesh.creation.box(extents=[40, 40, 10])
        b2.apply_translation([0, 0, 15])
        b3 = trimesh.creation.box(extents=[60, 60, 10])
        b3.apply_translation([0, 0, 5])
        mesh_to_slice = trimesh.util.concatenate([b1, b2, b3])

    if mesh_to_slice is not None:
        # 1. Center mesh XY at (0, 0) and bottom at Z=0
        mesh_to_slice.apply_translation([-mesh_to_slice.centroid[0], -mesh_to_slice.centroid[1], -mesh_to_slice.bounds[0][2]])

        # 2. Apply Orientation / Rotations (around mesh center)
        if any(abs(r) > 0.01 for r in [rot_x, rot_y, rot_z]):
            rx, ry, rz = np.radians(rot_x), np.radians(rot_y), np.radians(rot_z)
            rot_mat = trimesh.transformations.euler_matrix(rx, ry, rz)
            mesh_to_slice.apply_transform(rot_mat)

        # 3. Apply Scale
        if any(abs(s - 1.0) > 0.001 for s in [scale_x, scale_y, scale_z]):
            mesh_to_slice.apply_scale([scale_x, scale_y, scale_z])

        # 4. Position model on stock floor
        mesh_to_slice.apply_translation([
            -mesh_to_slice.centroid[0] + target_cx,
            -mesh_to_slice.centroid[1] + target_cy,
            -mesh_to_slice.bounds[0][2] + pos_z
        ])

        if shape_enum == StockShape.CONFORMING:
            margin_xy = float(data.get("margin_xy", 5.0))
            analyzer = DesignAnalyzer()
            conforming_poly = analyzer.compute_conforming_stock_outline(mesh_to_slice, margin_xy=margin_xy)
            stock.conforming_polygon = conforming_poly

        result = slicer.slice_3d_mesh(
            mesh=mesh_to_slice,
            z_bottom=0.0,
            custom_stepdown=stepdown,
            clearing_pattern=clearing_pattern,
            stock_to_leave_mm=stock_to_leave,
            tabs_count=tabs_count,
            tab_width_mm=tab_width,
            tab_height_mm=tab_height
        )
    else:
        # Sample 1: Block with circular hole and oblong slot centered around (target_cx, target_cy)
        cx, cy = target_cx, target_cy
        hole = sg.Point(cx - stock_w * 0.25, cy).buffer(8.0)
        slot = sg.box(cx + 5.0, cy - 5.0, cx + stock_w * 0.35, cy + 5.0).buffer(3.0)
        perimeter = sg.box(cx - stock_w / 2.0 + 5.0, cy - stock_l / 2.0 + 5.0, cx + stock_w / 2.0 - 5.0, cy + stock_l / 2.0 - 5.0)

        result = slicer.slice_25d_pockets_and_profiles(
            pockets=[slot],
            holes=[hole],
            perimeter=perimeter,
            target_depth_mm=stock_t - 2.0,
            custom_stepdown=stepdown
        )

    latest_slicing_result = result
    latest_stock = stock

    # Serialize layers for frontend WebGL rendering
    layers_data = []
    for lyr in result.layers:
        segs_data = []
        for seg in lyr.segments:
            pts_data = [
                {"x": pt.x, "y": pt.y, "z": pt.z, "move_type": pt.move_type.value}
                for pt in seg.points
            ]
            segs_data.append({
                "operation": seg.operation.value,
                "is_closed": seg.is_closed,
                "points": pts_data
            })
        layers_data.append({
            "layer_index": lyr.layer_index,
            "nominal_z": lyr.nominal_z,
            "segments": segs_data
        })

    return jsonify({
        "success": True,
        "layer_count": result.layer_count,
        "cut_distance_mm": result.total_cut_length_mm,
        "rapid_distance_mm": result.total_rapid_length_mm,
        "estimated_minutes": result.estimated_time_minutes,
        "layers": layers_data
    })


@app.post("/api/model/upload_stl")
def upload_stl():
    """Upload an arbitrary 3D model (.stl or .obj) to slice and simulate."""
    global current_loaded_mesh, current_mesh_metadata
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "Empty filename."}), 400

    filename = f.filename.lower()
    file_type = "stl" if filename.endswith(".stl") else "obj" if filename.endswith(".obj") else None
    if not file_type:
        return jsonify({"error": "Unsupported file format. Please upload .STL or .OBJ"}), 400

    try:
        data_bytes = f.read()
        mesh = trimesh.load(io.BytesIO(data_bytes), file_type=file_type)

        if isinstance(mesh, trimesh.Scene):
            # Combine scene geometries into a single mesh
            geoms = [g for g in mesh.geometry.values() if isinstance(g, trimesh.Trimesh)]
            if not geoms:
                return jsonify({"error": "The 3D model contains no mesh geometry."}), 400
            mesh = trimesh.util.concatenate(geoms)

        # Center mesh base on XY, and place bottom on Z=0
        bounds = mesh.bounds
        extents = mesh.extents
        center = mesh.centroid

        # Translate so bottom sits at Z=0
        mesh.apply_translation([-center[0], -center[1], -bounds[0][2]])

        current_loaded_mesh = mesh
        current_mesh_metadata = {
            "name": f.filename,
            "width": float(extents[0]),
            "length": float(extents[1]),
            "height": float(extents[2]),
            "vertices_count": len(mesh.vertices),
            "faces_count": len(mesh.faces)
        }

        # Decimate / simplify preview vertices if mesh is huge (to keep WebGL responsive)
        preview_mesh = mesh
        if len(preview_mesh.faces) > 20000:
            try:
                preview_mesh = preview_mesh.simplify_quadric_decimation(20000)
            except Exception:
                pass

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(mesh, margin_xy=5.0, margin_z_top=2.0)

        return jsonify({
            "success": True,
            "metadata": current_mesh_metadata,
            "analysis": {
                "detected_shape": analysis.detected_shape,
                "shape_description": analysis.shape_description,
                "circularity": analysis.circularity,
                "recommended_box": analysis.recommended_box,
                "recommended_cylinder": analysis.recommended_cylinder,
                "recommended_plate": analysis.recommended_plate,
                "recommended_round_plate": analysis.recommended_round_plate,
                "footprint_area_mm2": analysis.footprint_area_mm2,
                "volume_cm3": analysis.volume_cm3,
                "min_cylinder_diameter_mm": analysis.min_cylinder_diameter_mm,
                "silhouette_exterior": analysis.silhouette_exterior,
                "silhouette_interiors": analysis.silhouette_interiors,
                "feature_edges": analysis.feature_edges,
                "detected_holes": analysis.detected_holes,
                "min_hole_diameter_mm": analysis.min_hole_diameter_mm,
                "has_top_holes": analysis.has_top_holes
            },
            "vertices": preview_mesh.vertices.tolist(),
            "faces": preview_mesh.faces.tolist()
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to parse 3D model: {exc}"}), 400


@app.get("/api/model/sample_pyramid")
def sample_pyramid():
    """Load the preset 3-Tier Stepped Pyramid STL model."""
    global current_loaded_mesh, current_mesh_metadata
    fixture_candidates = [
        ROOT_DIR / "tests" / "fixtures" / "stepped_pyramid.stl",
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "stepped_pyramid.stl"
    ]
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            fixture_candidates.insert(0, Path(sys._MEIPASS) / "tests" / "fixtures" / "stepped_pyramid.stl")
        fixture_candidates.insert(1, Path(sys.executable).parent / "tests" / "fixtures" / "stepped_pyramid.stl")
        fixture_candidates.insert(2, Path(sys.executable).parent / "_internal" / "tests" / "fixtures" / "stepped_pyramid.stl")
    fixture_path = next((f for f in fixture_candidates if f.exists()), fixture_candidates[0])
    if not fixture_path.exists():
        return jsonify({"error": "Fixture not found"}), 404

    mesh = trimesh.load(fixture_path, file_type="stl")
    bounds = mesh.bounds
    center = mesh.centroid
    mesh.apply_translation([-center[0], -center[1], -bounds[0][2]])

    current_loaded_mesh = mesh
    current_mesh_metadata = {
        "name": "stepped_pyramid.stl",
        "width": float(mesh.extents[0]),
        "length": float(mesh.extents[1]),
        "height": float(mesh.extents[2]),
        "vertices_count": len(mesh.vertices),
        "faces_count": len(mesh.faces)
    }

    analyzer = DesignAnalyzer(crease_angle_deg=25.0)
    analysis = analyzer.analyze(mesh, margin_xy=5.0, margin_z_top=2.0)

    return jsonify({
        "success": True,
        "metadata": current_mesh_metadata,
        "analysis": {
            "detected_shape": analysis.detected_shape,
            "shape_description": analysis.shape_description,
            "circularity": analysis.circularity,
            "recommended_box": analysis.recommended_box,
            "recommended_cylinder": analysis.recommended_cylinder,
            "recommended_plate": analysis.recommended_plate,
            "recommended_round_plate": analysis.recommended_round_plate,
            "footprint_area_mm2": analysis.footprint_area_mm2,
            "volume_cm3": analysis.volume_cm3,
            "min_cylinder_diameter_mm": analysis.min_cylinder_diameter_mm,
            "silhouette_exterior": analysis.silhouette_exterior,
            "silhouette_interiors": analysis.silhouette_interiors,
            "feature_edges": analysis.feature_edges,
            "detected_holes": analysis.detected_holes,
            "min_hole_diameter_mm": analysis.min_hole_diameter_mm,
            "has_top_holes": analysis.has_top_holes
        },
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist()
    })


@app.post("/api/model/layflat")
def layflat_model():
    """Orient currently loaded mesh so its largest planar face sits flat on spoilboard bed (Z=0)."""
    global current_loaded_mesh, current_mesh_metadata
    if current_loaded_mesh is None:
        return jsonify({"error": "No mesh loaded to lay flat."}), 400

    try:
        mesh = current_loaded_mesh.copy()
        facets = mesh.facets
        if len(facets) > 0:
            facet_areas = [mesh.area_faces[facet].sum() for facet in facets]
            best_facet_idx = int(np.argmax(facet_areas))
            best_facet = facets[best_facet_idx]
            normal = mesh.face_normals[best_facet].mean(axis=0)
            norm_val = np.linalg.norm(normal)
            if norm_val > 1e-6:
                normal = normal / norm_val
            else:
                normal = np.array([0.0, 0.0, -1.0])
        else:
            largest_face_idx = int(np.argmax(mesh.area_faces))
            normal = mesh.face_normals[largest_face_idx]
            norm_val = np.linalg.norm(normal)
            if norm_val > 1e-6:
                normal = normal / norm_val
            else:
                normal = np.array([0.0, 0.0, -1.0])

        # Target normal facing downward onto bed: [0, 0, -1]
        target_normal = np.array([0.0, 0.0, -1.0])
        # If normal is not already downward, align it
        if np.dot(normal, target_normal) < 0.9999:
            rot_mat = trimesh.geometry.align_vectors(normal, target_normal)
            mesh.apply_transform(rot_mat)

        # Re-center at XY (0, 0) and bottom at Z=0
        mesh.apply_translation([-mesh.centroid[0], -mesh.centroid[1], -mesh.bounds[0][2]])

        current_loaded_mesh = mesh
        current_mesh_metadata["width"] = float(mesh.extents[0])
        current_mesh_metadata["length"] = float(mesh.extents[1])
        current_mesh_metadata["height"] = float(mesh.extents[2])

        preview_mesh = mesh
        if len(preview_mesh.faces) > 20000:
            try:
                preview_mesh = preview_mesh.simplify_quadric_decimation(20000)
            except Exception:
                pass

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(mesh, margin_xy=5.0, margin_z_top=2.0)

        return jsonify({
            "success": True,
            "metadata": current_mesh_metadata,
            "analysis": {
                "detected_shape": analysis.detected_shape,
                "shape_description": analysis.shape_description,
                "circularity": analysis.circularity,
                "recommended_box": analysis.recommended_box,
                "recommended_cylinder": analysis.recommended_cylinder,
                "recommended_plate": analysis.recommended_plate,
                "recommended_round_plate": analysis.recommended_round_plate,
                "footprint_area_mm2": analysis.footprint_area_mm2,
                "volume_cm3": analysis.volume_cm3,
                "min_cylinder_diameter_mm": analysis.min_cylinder_diameter_mm,
                "silhouette_exterior": analysis.silhouette_exterior,
                "silhouette_interiors": analysis.silhouette_interiors,
                "feature_edges": analysis.feature_edges,
                "detected_holes": analysis.detected_holes,
                "min_hole_diameter_mm": analysis.min_hole_diameter_mm,
                "has_top_holes": analysis.has_top_holes
            },
            "vertices": preview_mesh.vertices.tolist(),
            "faces": preview_mesh.faces.tolist()
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to lay model flat: {exc}"}), 500


@app.post("/api/model/analyze")
def analyze_model():
    """Analyze loaded 3D design to extract bounding extents, volume, 2D silhouette, convex hull,
    feature edge curves, and recommended stock sizes for box, cylinder, and metal plates.
    """
    global current_loaded_mesh
    if current_loaded_mesh is None:
        return jsonify({"error": "No 3D model currently loaded to analyze."}), 400

    try:
        data = request.get_json(silent=True) or {}
        margin_xy = float(data.get("margin_xy", 5.0))
        margin_z_top = float(data.get("margin_z_top", 2.0))
        margin_z_bottom = float(data.get("margin_z_bottom", 0.0))

        # Apply any current orientation or scale from client
        rot_x = float(data.get("rot_x", 0.0))
        rot_y = float(data.get("rot_y", 0.0))
        rot_z = float(data.get("rot_z", 0.0))
        scale_x = float(data.get("scale_x", 1.0))
        scale_y = float(data.get("scale_y", 1.0))
        scale_z = float(data.get("scale_z", 1.0))

        mesh = current_loaded_mesh.copy()
        if any(abs(r) > 0.01 for r in [rot_x, rot_y, rot_z]):
            rx, ry, rz = np.radians(rot_x), np.radians(rot_y), np.radians(rot_z)
            mesh.apply_transform(trimesh.transformations.euler_matrix(rx, ry, rz))
        if any(abs(s - 1.0) > 0.001 for s in [scale_x, scale_y, scale_z]):
            mesh.apply_scale([scale_x, scale_y, scale_z])

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(
            mesh,
            margin_xy=margin_xy,
            margin_z_top=margin_z_top,
            margin_z_bottom=margin_z_bottom
        )

        return jsonify({
            "success": True,
            "width_x": analysis.width_x,
            "length_y": analysis.length_y,
            "height_z": analysis.height_z,
            "min_x": analysis.min_x,
            "max_x": analysis.max_x,
            "min_y": analysis.min_y,
            "max_y": analysis.max_y,
            "min_z": analysis.min_z,
            "max_z": analysis.max_z,
            "centroid": analysis.centroid,
            "volume_cm3": analysis.volume_cm3,
            "surface_area_cm2": analysis.surface_area_cm2,
            "footprint_area_mm2": analysis.footprint_area_mm2,
            "perimeter_length_mm": analysis.perimeter_length_mm,
            "circularity": analysis.circularity,
            "detected_shape": analysis.detected_shape,
            "shape_description": analysis.shape_description,
            "triangle_count": analysis.triangle_count,
            "vertex_count": analysis.vertex_count,
            "is_watertight": analysis.is_watertight,
            "min_cylinder_diameter_mm": analysis.min_cylinder_diameter_mm,
            "recommended_box": analysis.recommended_box,
            "recommended_cylinder": analysis.recommended_cylinder,
            "recommended_plate": analysis.recommended_plate,
            "recommended_round_plate": analysis.recommended_round_plate,
            "silhouette_exterior": analysis.silhouette_exterior,
            "silhouette_interiors": analysis.silhouette_interiors,
            "convex_hull": analysis.convex_hull,
            "feature_edges": analysis.feature_edges,
            "detected_holes": analysis.detected_holes,
            "min_hole_diameter_mm": analysis.min_hole_diameter_mm,
            "has_top_holes": analysis.has_top_holes
        })
    except Exception as exc:
        return jsonify({"error": f"Design analysis failed: {exc}"}), 500


@app.post("/api/slicer/auto_setup")
def auto_setup():
    """Auto-select optimal tool, machining strategy, speeds & feeds, and cutting parameters
    based on current 3D geometry analysis and active CNC machine profile.
    """
    global current_loaded_mesh, active_machine
    if current_loaded_mesh is None:
        return jsonify({"error": "No 3D model loaded to optimize for."}), 400

    try:
        data = request.get_json(silent=True) or {}
        machine_id = data.get("machine_id")
        machine = STANDARD_CNC_MACHINES.get(machine_id, active_machine)

        # Apply any current client orientation/scale
        rot_x = float(data.get("rot_x", 0.0))
        rot_y = float(data.get("rot_y", 0.0))
        rot_z = float(data.get("rot_z", 0.0))
        scale_x = float(data.get("scale_x", 1.0))
        scale_y = float(data.get("scale_y", 1.0))
        scale_z = float(data.get("scale_z", 1.0))

        mesh = current_loaded_mesh.copy()
        if any(abs(r) > 0.01 for r in [rot_x, rot_y, rot_z]):
            rx, ry, rz = np.radians(rot_x), np.radians(rot_y), np.radians(rot_z)
            mesh.apply_transform(trimesh.transformations.euler_matrix(rx, ry, rz))
        if any(abs(s - 1.0) > 0.001 for s in [scale_x, scale_y, scale_z]):
            mesh.apply_scale([scale_x, scale_y, scale_z])

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(mesh)

        recommendation = AutoPilot.compute_recommendations(analysis, machine)
        res = recommendation.to_dict()
        res["success"] = True
        return jsonify(res)
    except Exception as exc:
        return jsonify({"error": f"Auto setup calculation failed: {exc}"}), 500


@app.get("/api/optimizer/options")
def optimizer_options():
    """Return available profiles, materials, and finish presets."""
    return jsonify({
        "success": True,
        "profiles": MachiningOptimizer.PROFILES,
        "materials": {k: m.to_dict() for k, m in MATERIALS.items()},
        "finishes": MachiningOptimizer.FINISH_LEVELS
    })


@app.post("/api/optimizer/analyze")
def optimizer_analyze():
    """Run multi-objective optimization analysis comparing baseline vs optimized parameters."""
    global current_loaded_mesh, active_machine
    if current_loaded_mesh is None:
        return jsonify({"error": "No 3D model currently loaded."}), 400

    try:
        data = request.get_json(silent=True) or {}
        profile_key = data.get("profile", "balanced")
        material_key = data.get("material", "wood_mdf")
        finish_key = data.get("finish", "standard")
        gcode_opts = data.get("gcode_opts", {})
        machine_id = data.get("machine_id")
        machine = STANDARD_CNC_MACHINES.get(machine_id, active_machine)

        # Apply any current transformations
        rot_x = float(data.get("rot_x", 0.0))
        rot_y = float(data.get("rot_y", 0.0))
        rot_z = float(data.get("rot_z", 0.0))
        scale_x = float(data.get("scale_x", 1.0))
        scale_y = float(data.get("scale_y", 1.0))
        scale_z = float(data.get("scale_z", 1.0))

        mesh = current_loaded_mesh.copy()
        if any(abs(r) > 0.01 for r in [rot_x, rot_y, rot_z]):
            rx, ry, rz = np.radians(rot_x), np.radians(rot_y), np.radians(rot_z)
            mesh.apply_transform(trimesh.transformations.euler_matrix(rx, ry, rz))
        if any(abs(s - 1.0) > 0.001 for s in [scale_x, scale_y, scale_z]):
            mesh.apply_scale([scale_x, scale_y, scale_z])

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(mesh)

        recommendation = MachiningOptimizer.compute(
            analysis=analysis,
            machine=machine,
            profile_key=profile_key,
            material_key=material_key,
            finish_key=finish_key,
            gcode_opts=gcode_opts
        )
        res = recommendation.to_dict()
        res["success"] = True
        return jsonify(res)
    except Exception as exc:
        return jsonify({"error": f"Optimization analysis failed: {exc}"}), 500


@app.post("/api/optimizer/apply")
def optimizer_apply():
    """Apply optimized parameters, slice model with optimized toolpaths, and generate arc-fitted G-code."""
    global current_loaded_mesh, active_machine, latest_slicing_result, latest_stock, latest_optimized_gcode, latest_optimizer_report
    if current_loaded_mesh is None:
        return jsonify({"error": "No 3D model loaded to optimize and slice."}), 400

    try:
        data = request.get_json(silent=True) or {}
        profile_key = data.get("profile", "balanced")
        material_key = data.get("material", "wood_mdf")
        finish_key = data.get("finish", "standard")
        gcode_opts = data.get("gcode_opts", {})
        machine_id = data.get("machine_id")
        machine = STANDARD_CNC_MACHINES.get(machine_id, active_machine)

        # Transforms & orientation
        rot_x = float(data.get("rot_x", 0.0))
        rot_y = float(data.get("rot_y", 0.0))
        rot_z = float(data.get("rot_z", 0.0))
        scale_x = float(data.get("scale_x", 1.0))
        scale_y = float(data.get("scale_y", 1.0))
        scale_z = float(data.get("scale_z", 1.0))
        pos_z = float(data.get("pos_z", 0.0))

        mesh = current_loaded_mesh.copy()
        # Center mesh XY at (0, 0) and bottom at Z=0
        mesh.apply_translation([-mesh.centroid[0], -mesh.centroid[1], -mesh.bounds[0][2]])
        if any(abs(r) > 0.01 for r in [rot_x, rot_y, rot_z]):
            rx, ry, rz = np.radians(rot_x), np.radians(rot_y), np.radians(rot_z)
            mesh.apply_transform(trimesh.transformations.euler_matrix(rx, ry, rz))
        if any(abs(s - 1.0) > 0.001 for s in [scale_x, scale_y, scale_z]):
            mesh.apply_scale([scale_x, scale_y, scale_z])

        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(mesh)

        recommendation = MachiningOptimizer.compute(
            analysis=analysis,
            machine=machine,
            profile_key=profile_key,
            material_key=material_key,
            finish_key=finish_key,
            gcode_opts=gcode_opts
        )

        stock_shape_str = data.get("stock_shape", "box")
        shape_enum = StockShape(stock_shape_str) if stock_shape_str in [s.value for s in StockShape] else StockShape.BOX

        stock_w = float(data.get("stock_w", max(70.0, analysis.width_x + 10.0)))
        stock_l = float(data.get("stock_l", max(70.0, analysis.length_y + 10.0)))
        stock_t = float(data.get("stock_t", max(32.0, analysis.height_z + 2.0)))
        stock_d = float(data.get("stock_d", max(90.0, analysis.min_cylinder_diameter_mm + 10.0)))

        stock = StockBlock(
            shape=shape_enum,
            width_x=stock_w,
            length_y=stock_l,
            thickness_z=stock_t,
            diameter_d=stock_d,
            origin_x=-stock_w / 2.0,
            origin_y=-stock_l / 2.0
        )
        stock.set_center(0.0, 0.0)

        # Position mesh at (0, 0, pos_z)
        mesh.apply_translation([-mesh.centroid[0], -mesh.centroid[1], -mesh.bounds[0][2] + pos_z])

        tool = CuttingTool(
            tool_id=1,
            name=recommendation.tool_name,
            tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=recommendation.tool_diameter_mm,
            stepdown_mm=recommendation.stepdown_mm,
            stepover_fraction=recommendation.stepover_pct / 100.0,
            feedrate_xy_mm_min=recommendation.feed_xy_mm_min,
            plunge_rate_z_mm_min=recommendation.feed_z_mm_min
        )

        slicer = SubtractiveSlicer(
            stock=stock,
            tool=tool,
            simplification_tolerance=recommendation.smoothing_tolerance_mm
        )

        result = slicer.slice_3d_mesh(
            mesh=mesh,
            z_bottom=0.0,
            custom_stepdown=recommendation.stepdown_mm,
            clearing_pattern=recommendation.clearing_pattern,
            stock_to_leave_mm=recommendation.stock_to_leave_mm,
            tabs_count=recommendation.tabs_count,
            tab_width_mm=recommendation.tab_width_mm,
            tab_height_mm=recommendation.tab_height_mm
        )

        # Run G-Code Optimization (Arc Fitting & Micro-Movement Filter)
        gcode_opt = GCodeOptimizer(
            enable_arc_fitting=recommendation.arc_fitting_enabled,
            enable_micro_filter=recommendation.micro_move_filter_enabled,
            enable_collinear_merge=recommendation.collinear_merge_enabled,
            tolerance_mm=recommendation.smoothing_tolerance_mm
        )
        opt_report = gcode_opt.optimize_slicing_result(result)
        opt_gcode_text = gcode_opt.generate_optimized_gcode(
            result,
            spindle_rpm=recommendation.spindle_rpm,
            program_name=f"{machine.id.upper()}_AUTO_OPT"
        )

        latest_slicing_result = result
        latest_stock = stock
        latest_optimized_gcode = opt_gcode_text
        latest_optimizer_report = {
            "recommendation": recommendation.to_dict(),
            "gcode_optimization": opt_report
        }

        # Serialize layers for Three.js WebGL visualization
        layers_data = []
        for lyr in result.layers:
            segs_data = []
            for seg in lyr.segments:
                pts_data = [
                    {"x": pt.x, "y": pt.y, "z": pt.z, "move_type": pt.move_type.value}
                    for pt in seg.points
                ]
                segs_data.append({
                    "operation": seg.operation.value,
                    "is_closed": seg.is_closed,
                    "points": pts_data
                })
            layers_data.append({
                "layer_index": lyr.layer_index,
                "nominal_z": lyr.nominal_z,
                "segments": segs_data
            })

        return jsonify({
            "success": True,
            "layer_count": result.layer_count,
            "cut_distance_mm": result.total_cut_length_mm,
            "rapid_distance_mm": result.total_rapid_length_mm,
            "estimated_minutes": recommendation.optimized_metrics.cycle_time_min,
            "layers": layers_data,
            "recommendation": recommendation.to_dict(),
            "gcode_stats": {
                "original_moves": opt_report["original_points_count"],
                "optimized_moves": opt_report["optimized_moves_count"],
                "micro_moves_eliminated": opt_report["micro_moves_eliminated"],
                "fitted_arcs_count": opt_report["fitted_arcs_count"],
                "reduction_pct": opt_report["line_reduction_percent"],
                "lines_count": len(opt_gcode_text.splitlines())
            }
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to apply optimization and slice: {exc}"}), 500


@app.get("/api/gcode/text")
def gcode_text():
    """Return the generated G-code text with line count for in-app inspection."""
    global latest_slicing_result, latest_optimized_gcode
    if latest_optimized_gcode is not None:
        return jsonify({
            "gcode": latest_optimized_gcode,
            "lines": len(latest_optimized_gcode.splitlines()),
            "is_optimized": True
        })
    if latest_slicing_result is None:
        return jsonify({"gcode": "; No toolpaths generated yet.\n; Load a 3D model and click Slice Stock.\n", "lines": 2})

    dialect = active_machine.controller_dialect
    gen = GCodeGenerator(dialect=dialect)
    text = gen.generate(
        latest_slicing_result,
        spindle_rpm=min(16000, active_machine.spindle_max_rpm),
        program_name=f"{active_machine.id.upper()}_JOB"
    )
    return jsonify({
        "gcode": text,
        "lines": len(text.splitlines()),
        "is_optimized": False
    })

@app.get("/api/gcode/verify")
def verify_gcode():
    """Run virtual CNC controller emulation to verify and validate the generated G-code."""
    global latest_slicing_result, latest_optimized_gcode
    if latest_optimized_gcode is not None:
        text = latest_optimized_gcode
    elif latest_slicing_result is not None:
        dialect = active_machine.controller_dialect
        gen = GCodeGenerator(dialect=dialect)
        text = gen.generate(
            latest_slicing_result,
            spindle_rpm=min(16000, active_machine.spindle_max_rpm),
            program_name=f"{active_machine.id.upper()}_JOB"
        )
    else:
        return jsonify({"error": "No toolpaths generated yet. Slice model first."}), 400

    validator = GCodeValidator(
        bed_width=active_machine.bed_width_x,
        bed_length=active_machine.bed_length_y,
        max_z_travel=active_machine.max_z_travel,
        machine_name=active_machine.name
    )
    report = validator.verify(text)
    return jsonify({
        "success": True,
        "report": report.to_dict()
    })


@app.get("/api/gcode/download")
def download_gcode():
    """Download machine-ready G-code for the latest sliced job."""
    global latest_slicing_result, latest_optimized_gcode
    if latest_optimized_gcode is not None:
        gcode_text = latest_optimized_gcode
    elif latest_slicing_result is not None:
        dialect = active_machine.controller_dialect
        gen = GCodeGenerator(dialect=dialect)
        gcode_text = gen.generate(
            latest_slicing_result,
            spindle_rpm=min(16000, active_machine.spindle_max_rpm),
            program_name=f"{active_machine.id.upper()}_JOB"
        )
    else:
        return "No job has been sliced yet.", 400

    mem = io.BytesIO(gcode_text.encode("utf-8"))
    return send_file(
        mem,
        mimetype="text/plain",
        as_attachment=True,
        download_name=f"{active_machine.id}_job.gcode"
    )


if __name__ == "__main__":
    print("==================================================")
    print("  CNSlicer — Subtractive Studio")
    print("  Open in Chromium / Browser: http://127.0.0.1:5000")
    print("==================================================")
    app.run(host="127.0.0.1", port=5000, debug=False)
