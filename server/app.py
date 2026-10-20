from __future__ import annotations
import io
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, request, send_file, send_from_directory
import numpy as np
import shapely.geometry as sg
import trimesh

from engine.lidar.mock_sensor import MockLidarSensor
from engine.lidar.scanner import ToolheadLidarScanner
from engine.slicer.stock import StockBlock
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.gcode.generator import GCodeGenerator

ROOT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = ROOT_DIR / "ui"
STATIC_DIR = UI_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(UI_DIR))

# Global state for latest sliced job and loaded 3D model
latest_slicing_result = None
latest_stock = None
current_loaded_mesh: trimesh.Trimesh | None = None
current_mesh_metadata: dict = {}


@app.get("/")
def index():
    return send_file(UI_DIR / "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


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
    global latest_slicing_result, latest_stock
    data = request.get_json(force=True) or {}

    model_type = data.get("model_type", "sample1")
    stock_w = float(data.get("stock_w", 100.0))
    stock_l = float(data.get("stock_l", 80.0))
    stock_t = float(data.get("stock_t", 20.0))
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

    stock = StockBlock(width_x=stock_w, length_y=stock_l, thickness_z=stock_t, origin_x=50.0, origin_y=50.0)
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

    slicer = SubtractiveSlicer(stock=stock, tool=tool)

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
            -mesh_to_slice.centroid[0] + (50.0 + stock_w / 2.0 + pos_x),
            -mesh_to_slice.centroid[1] + (50.0 + stock_l / 2.0 + pos_y),
            -mesh_to_slice.bounds[0][2] + pos_z
        ])

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
        # Sample 1: Block with circular hole and oblong slot
        cx, cy = 50 + stock_w / 2, 50 + stock_l / 2
        hole = sg.Point(50 + 25.0, cy).buffer(8.0)
        slot = sg.box(50 + stock_w - 35.0, cy - 5.0, 50 + stock_w - 15.0, cy + 5.0).buffer(3.0)
        perimeter = sg.box(55.0, 55.0, 50 + stock_w - 5.0, 50 + stock_l - 5.0)

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

        return jsonify({
            "success": True,
            "metadata": current_mesh_metadata,
            "vertices": preview_mesh.vertices.tolist(),
            "faces": preview_mesh.faces.tolist()
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to parse 3D model: {exc}"}), 400


@app.get("/api/model/sample_pyramid")
def sample_pyramid():
    """Load the preset 3-Tier Stepped Pyramid STL model."""
    global current_loaded_mesh, current_mesh_metadata
    fixture_path = ROOT_DIR / "tests" / "fixtures" / "stepped_pyramid.stl"
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

    return jsonify({
        "success": True,
        "metadata": current_mesh_metadata,
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

        return jsonify({
            "success": True,
            "metadata": current_mesh_metadata,
            "vertices": preview_mesh.vertices.tolist(),
            "faces": preview_mesh.faces.tolist()
        })
    except Exception as exc:
        return jsonify({"error": f"Failed to lay model flat: {exc}"}), 500


@app.get("/api/gcode/text")
def gcode_text():
    """Return the generated G-code text with line count for in-app inspection."""
    global latest_slicing_result
    if latest_slicing_result is None:
        return jsonify({"gcode": "; No toolpaths generated yet.\n; Load a 3D model and click Slice Stock.\n", "lines": 2})

    gen = GCodeGenerator(dialect="grbl")
    text = gen.generate(latest_slicing_result, spindle_rpm=16000, program_name="CNC_SLICER_JOB")
    return jsonify({
        "gcode": text,
        "lines": len(text.splitlines())
    })

@app.get("/api/gcode/download")
def download_gcode():
    """Download machine-ready G-code for the latest sliced job."""
    global latest_slicing_result
    if latest_slicing_result is None:
        return "No job has been sliced yet.", 400

    gen = GCodeGenerator(dialect="grbl")
    gcode_text = gen.generate(latest_slicing_result, spindle_rpm=16000, program_name="CNC_SLICER_JOB")

    mem = io.BytesIO(gcode_text.encode("utf-8"))
    return send_file(
        mem,
        mimetype="text/plain",
        as_attachment=True,
        download_name="cnc_slicer_job.gcode"
    )


if __name__ == "__main__":
    print("==================================================")
    print("  CNC Milling Slicer — Subtractive Studio")
    print("  Open in Chromium / Browser: http://127.0.0.1:5000")
    print("==================================================")
    app.run(host="127.0.0.1", port=5000, debug=False)
