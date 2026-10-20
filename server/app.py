from __future__ import annotations
import io
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, request, send_file, send_from_directory
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

    if model_type == "custom_stl" and current_loaded_mesh is not None:
        mesh = current_loaded_mesh.copy()
        target_cx = 50.0 + stock_w / 2.0
        target_cy = 50.0 + stock_l / 2.0
        mesh.apply_translation([target_cx, target_cy, 0.0])
        result = slicer.slice_3d_mesh(mesh=mesh, z_bottom=0.0, custom_stepdown=stepdown)
    elif model_type == "sample2":
        # Sample 2: 3-Tier Stepped Pyramid from solid stock
        b1 = trimesh.creation.box(extents=[20, 20, 10])
        b1.apply_translation([80, 80, 25])
        b2 = trimesh.creation.box(extents=[40, 40, 10])
        b2.apply_translation([80, 80, 15])
        b3 = trimesh.creation.box(extents=[60, 60, 10])
        b3.apply_translation([80, 80, 5])
        mesh = trimesh.util.concatenate([b1, b2, b3])

        result = slicer.slice_3d_mesh(mesh=mesh, z_bottom=0.0, custom_stepdown=stepdown)
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
