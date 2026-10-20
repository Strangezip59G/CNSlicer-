from __future__ import annotations
import os, json, uuid, re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from processor import process_video, trace_frame_to_desmos, trace_frame_lineart_to_desmos

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
ALLOWED = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.config["DESMOS_API_KEY"] = os.getenv("DESMOS_API_KEY", "")

@app.get("/")
def index():
    return render_template("index.html", desmos_api_key=app.config["DESMOS_API_KEY"])

@app.post("/api/upload")
def upload():
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="Choose a video file first."), 400
    ext = Path(secure_filename(file.filename)).suffix.lower()
    if ext not in ALLOWED:
        return jsonify(error=f"Unsupported format {ext or '(no extension)'}. Use MP4, MOV, MKV, AVI, or WEBM."), 400
    job_id = uuid.uuid4().hex[:12]
    path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(path)
    try:
        meta = process_video(path, OUTPUT_DIR / job_id, requested_seconds=float(request.form.get("seconds", "15")),
                             sample_fps=float(request.form.get("fps", "2")))
    except Exception as exc:
        path.unlink(missing_ok=True)
        return jsonify(error=f"Could not process video: {exc}"), 400
    meta["job_id"] = job_id
    (OUTPUT_DIR / job_id / "metadata.json").write_text(json.dumps(meta, indent=2))
    return jsonify(meta)

@app.post("/api/trace")
def trace():
    data = request.get_json(force=True)
    job_id = str(data.get("job_id", ""))
    frame_name = str(data.get("frame", ""))
    if not job_id.isalnum() or not frame_name.startswith("frame_") or not frame_name.endswith(".jpg"):
        return jsonify(error="Invalid job or frame identifier."), 400
    frame_path = OUTPUT_DIR / job_id / frame_name
    if not frame_path.exists():
        return jsonify(error="Frame not found. Upload the video again."), 404
    try:
        metadata_path = OUTPUT_DIR / job_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        result = trace_frame_to_desmos(
            frame_path,
            max_width=int(data.get("width", 640)),
            colors=int(data.get("colors", 8)),
            threshold=float(data.get("threshold", 0.0002)),
            max_segments=int(data.get("max_segments", 1200)),
            smoothing=int(data.get("smoothing", 2)),
            curve_smoothing=int(data.get("curve_smoothing", 6)),
            fill_regions=bool(data.get("fill_regions", True)),
            triangle_size=int(data.get("triangle_size", 18)),
            graph_width=metadata.get("source_width"),
            graph_height=metadata.get("source_height")
        )
        out = OUTPUT_DIR / job_id / "desmos_expressions.json"
        out.write_text(json.dumps(result, indent=2))
        return jsonify(result)
    except Exception as exc:
        return jsonify(error=f"Tracing failed: {exc}"), 400

@app.post("/api/animate/trace")
def trace_animation_frame():
    data = request.get_json(force=True)
    job_id = str(data.get("job_id", ""))
    frame_name = str(data.get("frame", ""))
    if not job_id.isalnum() or not re.fullmatch(r"frame_\d{4}\.jpg", frame_name):
        return jsonify(error="Invalid job or frame identifier."), 400
    frame_path = OUTPUT_DIR / job_id / frame_name
    if not frame_path.exists():
        return jsonify(error="Frame not found. Upload the video again."), 404
    try:
        metadata_path = OUTPUT_DIR / job_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        result = trace_frame_lineart_to_desmos(
            frame_path, max_width=640,
            max_segments=int(data.get("max_segments", 400)),
            smoothing=int(data.get("smoothing", 1)),
            curve_smoothing=int(data.get("curve_smoothing", 4)),
            graph_width=metadata.get("source_width"),
            graph_height=metadata.get("source_height"))
        cache_path = OUTPUT_DIR / job_id / f"lineart_{frame_path.stem}.json"
        cache_path.write_text(json.dumps(result))
        return jsonify(result)
    except Exception as exc:
        return jsonify(error=f"Line-art tracing failed: {exc}"), 400

@app.get("/outputs/<job_id>/<path:filename>")
def output_file(job_id, filename):
    if not job_id.isalnum():
        return "Invalid job", 400
    return send_from_directory(OUTPUT_DIR / job_id, filename)

@app.get("/api/download/<job_id>")
def download(job_id):
    if not job_id.isalnum():
        return "Invalid job", 400
    path = OUTPUT_DIR / job_id / "desmos_expressions.json"
    if not path.exists():
        return "Trace a frame first.", 404
    return send_from_directory(path.parent, path.name, as_attachment=True)

if __name__ == "__main__":
    print("Anime-to-Desmos Agent: http://127.0.0.1:5000")
    print("Tip: set DESMOS_API_KEY before launch to enable the embedded calculator.")
    app.run(host="127.0.0.1", port=5000, debug=False)
