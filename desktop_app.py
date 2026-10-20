from __future__ import annotations

import html
import json
import gzip
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import queue
import shutil
import subprocess
import threading
import multiprocessing
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
import base64
import ctypes
import sys
import time
from processor import (create_anime_part_segmenter, process_video,
                       trace_frame_lineart_to_desmos, trace_frame_to_desmos)
from cpu_renderer import render_animation_to_mp4

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
OUTPUT_DIR = ROOT / "outputs"
MP4_OUTPUT_DIR = ROOT / "output mp4"
DESMOS_SKIN_PATH = RESOURCE_DIR / "assets" / "desmos_ui_reference.png"


def _trace_worker_count(frame_count: int) -> tuple[int, int, int, int]:
    """Use all physical cores, while bounding parallel model inference by free RAM."""
    logical = max(1, os.cpu_count() or 1)
    physical = None
    inference_slots = 1
    try:
        import psutil
        physical = psutil.cpu_count(logical=False)
        available_gib = psutil.virtual_memory().available / (1024 ** 3)
        # Model weights are shared, but concurrent forwards need separate
        # activations. Keep 2 GiB free for Windows and the app; allow at most
        # two simultaneous forwards when memory permits.
        inference_slots = max(1, min(2, int(max(0.0, available_gib - 2.0) / 0.8)))
    except Exception:
        pass
    cores = max(1, min(logical, int(physical or logical)))
    workers = max(1, min(max(1, frame_count), cores))
    return workers, cores, logical, inference_slots


def _desmos_player_program(animation: list[dict] | int, fps: float) -> str:
    """Build a compact DesmosPlayer loader for the matching animation JSON file."""
    frame_count = animation if isinstance(animation, int) else len(animation)
    if frame_count < 1:
        raise ValueError("There are no traced frames to export.")
    playback_fps = max(1.0, min(float(fps), 30.0))
    return f'''// @desmosPlayer (v1.2)
// Anime-to-Desmos loader for DesmosPlayer.
// Install/enable the DesmosPlayer userscript and open desmos.com/calculator.
// Add a Program expression, paste this file, and click its round program icon.
// When prompted, choose the matching desmos_*_animation.json from this job folder.
// Use DesmosPlayer's Start/Stop buttons for playback; f is the frame counter.
(async () => {{
  try {{
    if (typeof window.showOpenFilePicker !== "function") {{
      throw new Error("Open this graph in Chrome or Edge to load the local animation JSON.");
    }}
    const [handle] = await window.showOpenFilePicker({{
      multiple: false,
      types: [{{ description: "Anime-to-Desmos animation", accept: {{ "application/json": [".json"] }} }}]
    }});
    const source = JSON.parse(await (await handle.getFile()).text());
    if (!Array.isArray(source.frames) || source.frames.length !== {frame_count}) {{
      throw new Error("Choose the matching animation JSON with {frame_count} frames.");
    }}
    const frameData = source.frames.map(frame => (frame.expressions || []).map(expression => {{
      const item = {{latex: expression.latex}};
      for (const key of ["color", "lineWidth", "lineOpacity", "fill", "fillOpacity", "lines", "points", "pointSize", "pointOpacity"]) {{
        if (expression[key] !== undefined) item[key] = expression[key];
      }}
      return item;
    }}));
    const savedFps = Number(source.sample_fps);
    const fps = Number.isFinite(savedFps) && savedFps > 0 ? Math.max(1, Math.min(savedFps, 30)) : {playback_fps:.6g};
    const delay = Math.max(1, Math.round(1000 / fps));
    const maxExpressions = frameData.reduce((max, frame) => Math.max(max, frame.length), 0);
    const frameIds = Array.from({{length: maxExpressions}}, (_, i) => `anime_frame_${{i}}`);
    if (!frameData.length || !frameData.some(frame => frame.length)) {{
      throw new Error("The selected animation has no graphable equations.");
    }}
    const lastFrame = frameData.length - 1;
    const updateFrame = frameIndex => {{
      const frame = frameData[frameIndex];
      const actions = frame.map((expression, i) => set(frameIds[i], {{
        ...expression,
        color: expression.color || "#111111",
        lineWidth: expression.lineWidth ?? 2,
        lines: expression.lines ?? true,
        points: expression.points ?? false,
        pointSize: expression.pointSize ?? 9,
        pointOpacity: expression.pointOpacity ?? 1,
        fill: expression.fill === true,
        fillOpacity: expression.fillOpacity ?? 0,
        lineOpacity: expression.lineOpacity ?? 1,
        hidden: false
      }}));
      for (let i = frame.length; i < frameIds.length; i++) actions.push(set(frameIds[i], {{latex: "0=1", color: "#111111", lineWidth: 1, fill: false, fillOpacity: 0, lineOpacity: 0, hidden: true}}));
      actions.push(setValue("anime_f", frameIndex));
      return actions;
    }};
    const program = [
      label("anime_loop"),
      [set("anime_f", {{latex: "f=0", sliderBounds: {{min: "0", max: String(lastFrame), step: "1"}}}}), ...updateFrame(0)],
      pause(delay),
      ...frameData.slice(1).flatMap((_, i) => [updateFrame(i + 1), pause(delay)]),
      goto("anime_loop", 9999)
    ];
    desmosPlayer(program, {{debugMode: false}});
  }} catch (error) {{
    if (error.name !== "AbortError") window.alert("Could not load the animation: " + error.message);
  }}
}})();
'''


def _stream_frame_expressions(cached: dict, width: int, height: int) -> list[dict]:
    scale = 10.0 / max(int(width), int(height), 1)
    expressions = []
    for chunk in cached.get("points", []):
        xs = chunk.get("x", [])
        ys = chunk.get("y", [])
        if not xs or not ys:
            continue
        x_values = ','.join(map(str, xs))
        y_values = ','.join(map(str, ys))
        x_expr = f"([{x_values}]-{width / 2:g})*{scale:.8g}"
        y_expr = f"({height / 2:g}-[{y_values}])*{scale:.8g}"
        expressions.append({
            "latex": f"({x_expr},{y_expr})", "color": "#111111", "lineWidth": 0,
            "lineOpacity": 1, "lines": False, "points": True, "pointSize": 2,
            "pointOpacity": 1, "fill": False, "fillOpacity": 0,
        })
    return expressions


def _save_stream_animation_json(points_dir: Path, frame_count: int, fps: float, output_path: Path, width: int, height: int) -> None:
    """Bundle compressed pixels into a single DesmosPlayer JSON using centered graph coordinates."""
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as target:
            target.write('{"mode":"sketch_full_fps_animation","sample_fps":')
            target.write(json.dumps(float(fps)))
            scale = 10.0 / max(int(width), int(height), 1)
            target.write(',"graph_bounds":')
            json.dump({"left": -width * scale / 2, "right": width * scale / 2,
                       "bottom": -height * scale / 2, "top": height * scale / 2}, target, separators=(",", ":"))
            target.write(',"frames":[')
            for index in range(frame_count):
                if index:
                    target.write(",")
                cache_path = points_dir / f"{index:08d}.json.gz"
                with gzip.open(cache_path, "rt", encoding="utf-8") as source:
                    cached = json.load(source)
                expressions = _stream_frame_expressions(cached, width, height)
                frame = {"time": index / max(float(fps), 1.0), "expressions": expressions}
                json.dump(frame, target, separators=(",", ":"))
            target.write("]}")
        temp_path.replace(output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


class AnimeDesmosDesktop:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Anime to Desmos Studio")
        self.root.geometry("1120x800")
        self.root.minsize(900, 680)
        self.root.configure(bg="#0b1020")
        self.events: queue.Queue = queue.Queue()
        self.video_path: Path | None = None
        self.job_dir: Path | None = None
        self.animation_output_dir: Path | None = None
        self.open_output_kind = "mp4"
        self.frames: list[dict] = []
        self.animation: list[dict] = []
        self.sample_fps = 2.0
        self.source_fps = None
        self.source_size: tuple[int, int] | None = None
        self.animation_mode = "color"
        self.mode = tk.StringVar(value="color")
        self.preview_image = None
        key_file = ROOT / "api keys.txt"
        environment_key = os.getenv("DESMOS_API_KEY", "").strip()
        try:
            file_key = key_file.read_text(encoding="utf-8").strip() if key_file.is_file() else ""
        except OSError:
            file_key = ""
        self.api_key = environment_key or file_key
        self.viewer_server = None
        self.video_writer = None
        self.video_output: Path | None = None
        self.video_animation_json: Path | None = None
        self.video_equation_frames: list[dict] | None = None
        self.video_equation_cache_dir: Path | None = None
        self.animation_output_path: Path | None = None
        self.video_fps = 2.0
        self.video_frame_count = 0
        self.video_render_kind = "desmos"
        self.video_size: tuple[int, int] | None = None
        self.video_lock = threading.Lock()
        self.stream_capture = None
        self.stream_source_fps = 0.0
        self.stream_source_width = 0
        self.stream_source_height = 0
        self.stream_frame_total = 0
        self.stream_frame_index = 0
        self.stream_points_dir: Path | None = None
        self._style()
        self._build()
        self._restore_latest_stream_render()
        self._apply_glass_backdrop()
        self._update_mode()
        self.root.after(100, self._poll_events)

    def _restore_latest_stream_render(self):
        """Restore the newest saved full-FPS MP4 that has its matching equation cache."""
        if not MP4_OUTPUT_DIR.is_dir() or not OUTPUT_DIR.is_dir():
            return
        videos = sorted(MP4_OUTPUT_DIR.glob("*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
        animation_files = sorted(OUTPUT_DIR.rglob("desmos_sketch_full_fps_animation.json"),
                                 key=lambda path: path.stat().st_mtime, reverse=True)
        for video in videos:
            if video.stat().st_size <= 1024:
                continue
            capture = cv2.VideoCapture(str(video))
            if not capture.isOpened():
                capture.release()
                continue
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 1.0)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            capture.release()
            for animation_json in animation_files:
                cache_dir = animation_json.parent / "frame_points"
                if not cache_dir.is_dir():
                    cache_dir = OUTPUT_DIR / ".stream_cache" / animation_json.parent.name
                if not cache_dir.is_dir():
                    continue
                cached_frames = sum(1 for _ in cache_dir.glob("*.json.gz"))
                if frame_count < 1 or cached_frames != frame_count:
                    continue
                self.video_output = video
                self.video_animation_json = animation_json
                self.video_equation_cache_dir = cache_dir
                self.video_equation_frames = [{"time": i / max(fps, 1.0)} for i in range(frame_count)]
                self.video_fps = fps
                self.stream_source_fps = fps
                self.stream_source_width = width
                self.stream_source_height = height
                self.video_equations_btn.configure(state="normal")
                self.open_output_kind = "mp4"
                self.open_btn.configure(text="Open MP4 folder", state="normal")
                self.status.configure(text=f"Latest full-FPS render ready to replay · {video.name}")
                return

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0b1220")
        style.configure("Card.TFrame", background="#121d2e", relief="flat")
        style.configure("TLabel", background="#0b1220", foreground="#f4f7fc", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#0b1220", foreground="#b1bfd2", font=("Segoe UI", 9))
        style.configure("Card.TLabel", background="#121d2e", foreground="#f4f7fc", font=("Segoe UI", 10))
        style.configure("CardMuted.TLabel", background="#121d2e", foreground="#aebed2", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#0b1220", foreground="#ffffff", font=("Segoe UI", 23, "bold"))
        style.configure("Eyebrow.TLabel", background="#0b1220", foreground="#70c7ff", font=("Segoe UI", 9, "bold"))
        style.configure("CardTitle.TLabel", background="#121d2e", foreground="#f4f7fc", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", background="#263750", foreground="#f0f5fc", borderwidth=0, focusthickness=0,
                        font=("Segoe UI", 9, "bold"), padding=(10, 7))
        style.map("TButton", background=[("active", "#344b68"), ("disabled", "#1b2738")],
                  foreground=[("disabled", "#8290a4")])
        style.configure("Accent.TButton", background="#426fe1", foreground="white", borderwidth=0,
                        font=("Segoe UI", 9, "bold"), padding=(12, 8))
        style.map("Accent.TButton", background=[("active", "#5685f5"), ("disabled", "#34405f")])
        style.configure("Mode.TRadiobutton", background="#121d2e", foreground="#d5deeb",
                        font=("Segoe UI", 9, "bold"), padding=(9, 7), indicatorcolor="#273650")
        style.map("Mode.TRadiobutton", background=[("selected", "#213755"), ("active", "#1a2b43")],
                  foreground=[("selected", "#ffffff")], indicatorcolor=[("selected", "#8a9bff")])
        style.configure("TEntry", fieldbackground="#0e1929", foreground="#f4f7fc", insertcolor="#ffffff", padding=6)
        style.configure("TSpinbox", fieldbackground="#0e1929", foreground="#f4f7fc", arrowcolor="#c1cde0", padding=5)
        style.configure("TProgressbar", troughcolor="#202d43", background="#74d5ff", bordercolor="#202d43", lightcolor="#74d5ff", darkcolor="#74d5ff")

    def _apply_glass_backdrop(self):
        """Apply crisp native window chrome without enabling a translucent backdrop."""
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            dwm = ctypes.windll.dwmapi
            # Keep the system title bar dark and corners rounded. The content stays opaque.
            dark = ctypes.c_int(1)
            corners = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
        except Exception:
            pass

    def _build(self):
        outer = ttk.Frame(self.root, padding=(24, 20))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="ANIMATION WORKSPACE  /  DESMOS", style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(outer, text="Anime to Desmos Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Turn video frames into smooth, mathematical drawings.", style="Muted.TLabel").pack(anchor="w", pady=(4, 15))

        controls = ttk.Frame(outer, style="Card.TFrame", padding=18)
        controls.pack(fill="x")
        self.file_label = ttk.Label(controls, text="Choose a video clip", style="Card.TLabel")
        self.file_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.choose_btn = ttk.Button(controls, text="Choose video…", command=self._choose_video)
        self.choose_btn.grid(row=0, column=1, sticky="e")
        mode_card = ttk.Frame(outer, style="Card.TFrame", padding=(18, 12))
        mode_card.pack(fill="x", pady=(10, 0))
        ttk.Label(mode_card, text="Animation style", style="Card.TLabel").pack(side="left", padx=(0, 12))
        self.color_mode = ttk.Radiobutton(mode_card, text="Color · triangle fills + outlines", variable=self.mode, value="color", style="Mode.TRadiobutton", command=self._update_mode)
        self.color_mode.pack(side="left", padx=(0, 8))
        self.sketch_mode = ttk.Radiobutton(mode_card, text="Sketch · black edge drawing", variable=self.mode, value="sketch", style="Mode.TRadiobutton", command=self._update_mode)
        self.sketch_mode.pack(side="left")
        self.mode_help = ttk.Label(mode_card, text="Full-color source shades, with a manageable mesh size.", style="CardMuted.TLabel")
        self.mode_help.pack(side="right", padx=8)

        ttk.Label(controls, text="Clip seconds", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.seconds = tk.StringVar(value="15")
        ttk.Spinbox(controls, from_=1, to=20, increment=1, textvariable=self.seconds, width=8).grid(row=1, column=1, sticky="w", pady=(12, 0))
        ttk.Label(controls, text="Sample rate (frames/sec)", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.fps = tk.StringVar(value="2")
        ttk.Spinbox(controls, from_=1, to=120, increment=1, textvariable=self.fps, width=8).grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.source_fps_btn = ttk.Button(controls, text="Use source FPS", command=self._use_source_fps)
        self.source_fps_btn.grid(row=2, column=2, padx=(10, 0), sticky="w")
        ttk.Label(controls, text="Outline detail (segments/frame)", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.segments = tk.StringVar(value="2400")
        ttk.Spinbox(controls, from_=100, to=5000, increment=100, textvariable=self.segments, width=8).grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.palette_label = ttk.Label(controls, text="Color groups", style="Card.TLabel")
        self.palette_label.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.palette_colors = tk.StringVar(value="12")
        self.palette_spin = ttk.Spinbox(controls, from_=2, to=32, increment=1, textvariable=self.palette_colors, width=8)
        self.palette_spin.grid(row=4, column=1, sticky="w", pady=(8, 0))
        self.triangle_label = ttk.Label(controls, text="Triangle size (px)", style="Card.TLabel")
        self.triangle_label.grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.triangle_size = tk.StringVar(value="28")
        self.triangle_spin = ttk.Spinbox(controls, from_=8, to=32, increment=2, textvariable=self.triangle_size, width=8)
        self.triangle_spin.grid(row=5, column=1, sticky="w", pady=(8, 0))
        self.extract_btn = ttk.Button(controls, text="Extract frames", style="Accent.TButton", command=self._extract)
        self.extract_btn.grid(row=0, column=3, rowspan=2, padx=(20, 0), sticky="ns")
        self.trace_btn = ttk.Button(controls, text="Build color animation", command=self._trace)
        self.trace_btn.grid(row=3, column=3, rowspan=2, padx=(20, 0), sticky="ns")
        self.stream_btn = ttk.Button(controls, text="Full-FPS Sketch MP4", command=self._open_streamer, state="disabled")
        self.stream_btn.grid(row=5, column=3, padx=(20, 0), pady=(8, 0), sticky="ew")
        self.cpu_render_btn = ttk.Button(controls, text="Render MP4 on CPU", command=self._render_cpu_mp4, state="disabled")
        self.cpu_render_btn.grid(row=6, column=3, padx=(20, 0), pady=(8, 0), sticky="ew")
        controls.columnconfigure(0, weight=1)

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(14, 0))
        left = ttk.Frame(body, style="Card.TFrame", padding=12)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="FRAME STRIP", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        list_wrap = ttk.Frame(left, style="Card.TFrame")
        list_wrap.pack(fill="both", expand=True)
        self.frame_list = tk.Listbox(list_wrap, width=27, height=20, bg="#0d1525", fg="#dce8fb", selectbackground="#596de6", selectforeground="#ffffff", borderwidth=0, highlightthickness=0, font=("Segoe UI", 10), activestyle="none")
        self.frame_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_wrap, command=self.frame_list.yview)
        scroll.pack(side="right", fill="y")
        self.frame_list.configure(yscrollcommand=scroll.set)
        self.frame_list.bind("<<ListboxSelect>>", self._show_preview)

        workspace = ttk.Frame(body)
        workspace.pack(side="left", fill="both", expand=True, padx=(14, 0))
        actions = ttk.Frame(workspace, style="Card.TFrame", padding=(8, 6))
        actions.pack(fill="x", pady=(0, 10))
        for column in range(3):
            actions.columnconfigure(column, weight=1, uniform="action")
        self.graph_btn = ttk.Button(actions, text="Open Desmos graph", style="Accent.TButton", command=self._open_desmos, state="disabled")
        self.graph_btn.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        self.jsxgraph_btn = ttk.Button(actions, text="Open JSXGraph preview", command=self._open_jsxgraph, state="disabled")
        self.jsxgraph_btn.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.video_equations_btn = ttk.Button(actions, text="Play video + equations", command=self._open_video_equations, state="disabled")
        self.video_equations_btn.grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        self.open_btn = ttk.Button(actions, text="Open output folder", command=self._open_output, state="disabled")
        self.open_btn.grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        self.load_mp4_btn = ttk.Button(actions, text="Import latest MP4", command=self._load_latest_mp4)
        self.load_mp4_btn.grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.player_btn = ttk.Button(actions, text="Create DesmosPlayer loader", command=self._export_player_loader)
        self.player_btn.grid(row=1, column=2, sticky="ew", padx=3, pady=3)

        workspace_body = ttk.Frame(workspace)
        workspace_body.pack(fill="both", expand=True)
        # Reserve more room for the live activity log and keep the preview from
        # consuming nearly all of the remaining workspace width.
        activity_panel = ttk.Frame(workspace_body, style="Card.TFrame", padding=12, width=360)
        activity_panel.pack(side="right", fill="both", expand=False, padx=(12, 0))
        activity_panel.pack_propagate(False)
        canvas_panel = ttk.Frame(workspace_body, style="Card.TFrame", padding=12)
        canvas_panel.pack(side="left", fill="both", expand=True)
        ttk.Label(canvas_panel, text="PREVIEW CANVAS", style="CardTitle.TLabel").pack(anchor="w")
        self.preview = tk.Label(canvas_panel, text="Choose a clip and extract frames to see a preview", bg="#0b1220", fg="#9eacc0", font=("Segoe UI", 11), bd=0, highlightthickness=1, highlightbackground="#2a3850")
        self.preview.pack(fill="both", expand=True, pady=(8, 10))
        log_panel = ttk.Frame(activity_panel, style="Card.TFrame")
        log_panel.pack(fill="both", expand=True)
        ttk.Label(log_panel, text="LIVE ACTIVITY", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 6))
        log_wrap = ttk.Frame(log_panel, style="Card.TFrame")
        log_wrap.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_wrap, height=14, width=30, bg="#07111d", fg="#8cf2be", insertbackground="#8cf2be",
                               selectbackground="#315a66", relief="flat", bd=0, wrap="word",
                               font=("Cascadia Mono", 9), state="disabled")
        self.log_box.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_wrap, command=self.log_box.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=log_scroll.set)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(14, 0))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status = ttk.Label(footer, text="Choose a video to begin.", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(6, 0))
        self._append_log("Ready. Choose a video and start a render.")
        ttk.Label(outer, text="Render MP4 on CPU draws the saved JSXGraph polygons and curves locally across multiple CPU cores. Sketch mode also offers a source-FPS Desmos capture path.", style="Muted.TLabel", wraplength=920).pack(anchor="w", pady=(12, 0))

    def _append_log(self, message: str):
        if not hasattr(self, "log_box"):
            return
        stamp = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 300:
            self.log_box.delete("1.0", f"{lines - 300}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _update_mode(self):
        color = self.mode.get() == "color"
        self.palette_spin.configure(state="normal" if color else "disabled")
        self.triangle_spin.configure(state="normal" if color else "disabled")
        self.palette_label.configure(foreground="#eef3fa" if color else "#64748b")
        self.triangle_label.configure(foreground="#eef3fa" if color else "#64748b")
        self.trace_btn.configure(text="Build color animation" if color else "Build sketch animation")
        if hasattr(self, "stream_btn"):
            self.stream_btn.configure(state="disabled" if color or not self.video_path else "normal")
        if hasattr(self, "cpu_render_btn"):
            self.cpu_render_btn.configure(state="normal" if self.animation else "disabled")
        self.mode_help.configure(text="Full-color triangle mesh and smooth colored outlines." if color else "Fine-detail curves preserve pupils, lashes, blush, lips, hair highlights, profiles, clothing, accessories, foliage, and lighting cues.")

    def _use_source_fps(self):
        if not self.source_fps:
            messagebox.showinfo("Source FPS unavailable", "Choose a video with a readable frame rate first.")
            return
        self.fps.set(f"{self.source_fps:.3f}".rstrip("0").rstrip("."))
        self.status.configure(text=f"Extraction set to {self.source_fps:.2f} FPS · extracts every source frame when the source rate is accurate.")

    def _set_video(self, selected):
        self.video_path = Path(selected)
        self.source_fps = None
        self.source_size = None
        cap = cv2.VideoCapture(str(self.video_path))
        total = 0
        width = height = 0
        if cap.isOpened():
            rate = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            self.source_fps = rate if rate > 0 else None
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            self.source_size = (width, height) if width > 0 and height > 0 else None
            cap.release()
            if self.source_fps:
                dimensions = f" · {width}×{height}" if self.source_size else ""
                self.status.configure(text=f"Video imported{dimensions} · {self.source_fps:.2f} FPS · {total:,} source frames. Use source FPS to extract every frame.")
        self.stream_btn.configure(state="normal" if self.mode.get() == "sketch" else "disabled")
        self.file_label.configure(text=self.video_path.name)
        if not self.source_fps:
            self.status.configure(text="Video selected, but its frame rate could not be read. Try another MP4.")

    def _choose_video(self):
        selected = filedialog.askopenfilename(title="Choose a video", filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")])
        if selected:
            self._set_video(selected)

    def _load_latest_mp4(self):
        candidates = [path for path in MP4_OUTPUT_DIR.glob("*.mp4") if path.is_file() and path.stat().st_size > 1024]
        if not candidates:
            messagebox.showinfo("No saved MP4", "Render a video first, then import the latest MP4 here.")
            return
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        self._set_video(latest)

    def _busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for widget in (self.choose_btn, self.extract_btn, self.trace_btn, self.stream_btn, self.cpu_render_btn, self.player_btn, self.load_mp4_btn, self.jsxgraph_btn, self.video_equations_btn, self.color_mode, self.sketch_mode, self.source_fps_btn):
            widget.configure(state=state)
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=100)
            self._update_mode()

    def _extract(self):
        if not self.video_path:
            messagebox.showinfo("Choose a video", "Choose the source video first.")
            return
        try:
            seconds, fps = float(self.seconds.get()), float(self.fps.get())
            if not 0.5 <= seconds <= 20 or not 1 <= fps <= 120:
                raise ValueError
        except ValueError:
            messagebox.showerror("Check the settings", "Use 0.5–20 seconds and 1–120 frames per second.")
            return
        self.sample_fps = fps
        self.frames.clear()
        self.animation.clear()
        self.frame_list.delete(0, "end")
        job_id = uuid.uuid4().hex[:12]
        self.job_dir = OUTPUT_DIR / job_id
        self._busy(True)
        self.status.configure(text="Extracting frames in the background…")
        def work():
            try:
                meta = process_video(self.video_path, self.job_dir, requested_seconds=seconds, sample_fps=fps)
                self.events.put(("extracted", meta))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _trace(self):
        if not self.frames or not self.job_dir:
            messagebox.showinfo("No frames yet", "Extract frames before building the animation.")
            return
        mode = self.mode.get()
        try:
            budget = int(self.segments.get())
            colors = int(self.palette_colors.get())
            triangle_size = int(self.triangle_size.get())
            if not 100 <= budget <= 5000 or not 2 <= colors <= 32 or not 8 <= triangle_size <= 32:
                raise ValueError
        except ValueError:
            messagebox.showerror("Check the settings", "Use 100–5000 outline segments, 2–32 color groups, and 8–32 px triangles.")
            return
        frames = list(self.frames)
        folder = self.job_dir
        sample_fps = self.sample_fps
        graph_width, graph_height = self.source_size or (None, None)
        self.animation.clear()
        self._busy(True)
        self.status.configure(text=f"Building {mode} animation from {len(frames)} frames in the background…")
        def work():
            animation: list[dict | None] = [None] * len(frames)
            try:
                segmenter = None
                if mode != "color":
                    model_started = time.perf_counter()
                    self.events.put(("status", "Loading anime part parser · first run downloads its model; sketch tracing will take longer."))
                    try:
                        segmenter = create_anime_part_segmenter(ROOT / "model cache")
                        self.events.put(("status", f"Anime part parser ready in {time.perf_counter()-model_started:.1f}s. Starting spline trace."))
                    except Exception as exc:
                        # Keep sketch generation usable if a model checkpoint is
                        # unavailable. VTracer can still produce the curves;
                        # the per-part facial budgets resume when the model loads.
                        reason = str(exc).splitlines()[0][:140]
                        self.events.put(("status", f"Anime part model unavailable; using VTracer outlines. {reason}"))

                workers, physical_cores, logical_cpus, inference_slots = _trace_worker_count(len(frames))
                segmenter_semaphore = threading.Semaphore(inference_slots) if segmenter is not None else None
                # Each thread shares the same read-only anime parser, avoiding
                # one heavyweight Mask2Former copy per worker. Only model
                # inference is bounded by currently available RAM; image edges
                # and spline work continue in parallel on the other frames.
                try:
                    cv2.setNumThreads(1)
                except Exception:
                    pass
                if segmenter is not None:
                    try:
                        import torch
                        torch.set_num_threads(max(1, min(4, logical_cpus // max(2, inference_slots * 2))))
                        try:
                            torch.set_num_interop_threads(1)
                        except RuntimeError:
                            pass
                    except ImportError:
                        pass
                parser_note = f" · shared parser, {inference_slots} inference slot(s)" if segmenter is not None else ""
                self.events.put(("status", f"Tracing {len(frames)} frames across {workers} CPU cores{parser_note}"))

                def trace_one(index: int, frame: dict):
                    frame_path = folder / frame["name"]
                    frame_started = time.perf_counter()
                    if mode == "color":
                        result = trace_frame_to_desmos(frame_path, max_width=960, colors=colors,
                            max_segments=budget, smoothing=1, curve_smoothing=4,
                            fill_regions=True, triangle_size=triangle_size,
                            graph_width=graph_width, graph_height=graph_height)
                    else:
                        result = trace_frame_lineart_to_desmos(frame_path, max_width=1920,
                            max_segments=budget, smoothing=1, curve_smoothing=4,
                            graph_width=graph_width, graph_height=graph_height,
                            segmenter=segmenter, segmenter_semaphore=segmenter_semaphore)
                    result["time"] = frame["time"]
                    cache = folder / f"{mode}_{Path(frame['name']).stem}.json"
                    cache.write_text(json.dumps(result), encoding="utf-8")
                    elapsed = time.perf_counter() - frame_started
                    return index, result, int(result.get("count", 0)), round(elapsed, 1), cache.name

                completed = 0
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="frame-trace") as pool:
                    futures = [pool.submit(trace_one, index, frame) for index, frame in enumerate(frames)]
                    for future in as_completed(futures):
                        index, result, curve_count, elapsed, filename = future.result()
                        animation[index] = result
                        completed += 1
                        self.events.put(("progress", (completed, len(frames), index + 1,
                                                        curve_count, elapsed, filename)))
                if any(frame is None for frame in animation):
                    raise RuntimeError("Frame tracing stopped before every source frame was saved.")
                # The pool completes out of order; keep timeline frames in their
                # original order for playback and equation export.
                animation = [frame for frame in animation if frame is not None]
                output = folder / f"desmos_{mode}_animation.json"
                output.write_text(json.dumps({"mode": f"{mode}_animation", "sample_fps": sample_fps,
                                              "trace_workers": workers, "frames": animation}), encoding="utf-8")
                player_output = folder / "desmos_player_program.js"
                player_output.write_text(_desmos_player_program(animation, sample_fps), encoding="utf-8")
                self.events.put(("traced", (animation, output, mode, player_output)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _render_cpu_mp4(self):
        if not self.animation or not self.animation_output_path or not self.animation_output_path.is_file():
            messagebox.showinfo("Build an animation first", "Create the Color or Sketch animation before rendering an MP4.")
            return
        if not self.job_dir or not self.frames:
            messagebox.showerror("Animation frames are missing", "Rebuild the animation so its cached equation frames are available.")
            return
        frame_paths = [
            self.job_dir / f"{self.animation_mode}_{Path(frame['name']).stem}.json"
            for frame in self.frames[:len(self.animation)]
        ]
        missing = next((path for path in frame_paths if not path.is_file()), None)
        if missing:
            messagebox.showerror("Animation frame is missing", f"Could not find cached equation frame:\n{missing.name}")
            return
        width, height = self.source_size or (
            int(self.animation[0].get("graph_bounds", {}).get("right", 0)),
            int(self.animation[0].get("graph_bounds", {}).get("top", 0)),
        )
        try:
            fps = float(self.sample_fps)
            if fps <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messagebox.showerror("Invalid frame rate", "Set a valid frame rate before rendering the MP4.")
            return
        timestamp = datetime.now().strftime("%H-%M-%S_%Y-%m-%d")
        output_path = MP4_OUTPUT_DIR / f"{timestamp}.mp4"
        self._busy(True)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=len(frame_paths), value=0)
        self.status.configure(text=f"CPU rendering {len(frame_paths)} frames across multiple cores…")
        self._append_log(f"CPU render started · {len(frame_paths)} equation frames at {fps:g} FPS")

        def work():
            try:
                info = render_animation_to_mp4(
                    frame_paths, output_path, fps, (width, height),
                    progress=lambda done, total: self.events.put(("cpu_render_progress", (done, total))),
                )
                self.events.put(("cpu_rendered", (info, self.animation_output_path, list(self.animation))))
            except Exception as exc:
                self.events.put(("error", f"CPU MP4 render failed: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, _event=None):
        selection = self.frame_list.curselection()
        if not selection or not self.job_dir:
            return
        path = self.job_dir / self.frames[selection[0]]["name"]
        frame = cv2.imread(str(path))
        if frame is None:
            return
        max_w, max_h = max(320, self.preview.winfo_width()), max(240, self.preview.winfo_height())
        h, w = frame.shape[:2]
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".png", frame)
        if ok:
            import base64
            self.preview_image = tk.PhotoImage(data=base64.b64encode(encoded.tobytes()))
            self.preview.configure(image=self.preview_image, text="")

    def _poll_events(self):
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status.configure(text=payload)
                    self._append_log(payload)
                elif event == "progress":
                    done, total, frame_index, curve_count, elapsed, filename = payload
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=total, value=done)
                    self.status.configure(text=f"Completed {done}/{total} · frame {frame_index} · {curve_count:,} curves · {elapsed:.1f}s")
                    self._append_log(f"Saved {filename} · {curve_count:,} curves · {elapsed:.1f}s · {done}/{total} complete")
                elif event == "extracted":
                    self._busy(False)
                    self.frames = payload["frames"]
                    if payload.get("source_width") and payload.get("source_height"):
                        self.source_size = (int(payload["source_width"]), int(payload["source_height"]))
                    self.frame_list.delete(0, "end")
                    for item in self.frames:
                        self.frame_list.insert("end", f"{item['time']:.2f} sec")
                    self.status.configure(text=f"Ready: {len(self.frames)} frames at {payload['sample_fps']} FPS. Select one to preview or build the animation.")
                    self._append_log(f"Extracted {len(self.frames)} frames from {payload.get('source_width')}×{payload.get('source_height')} video.")
                    self.open_btn.configure(state="normal")
                    if self.frames:
                        self.frame_list.selection_set(0)
                        self._show_preview()
                elif event == "traced":
                    self._busy(False)
                    self.animation, output, finished_mode, player_output = payload
                    self.animation_output_path = output
                    self.video_animation_json = None
                    self.video_equation_frames = None
                    self.video_equations_btn.configure(state="disabled")
                    self.animation_mode = finished_mode
                    self.animation_output_dir = self.job_dir
                    self.open_output_kind = "animation"
                    self.open_btn.configure(text="Open animation folder")
                    self.status.configure(text=f"Done. {finished_mode.title()} mode · {len(self.animation)} frames saved.")
                    self._append_log(f"Animation complete · {len(self.animation)} frames · {output.name}")
                    self.open_btn.configure(state="normal")
                    self.graph_btn.configure(state="normal")
                    self.jsxgraph_btn.configure(state="normal")
                    self.cpu_render_btn.configure(state="normal")
                    self.player_btn.configure(state="normal")
                    program_size_mb = player_output.stat().st_size / (1024 * 1024)
                    size_note = "\n\nThis program is large; Desmos may take a long time to accept or run it." if program_size_mb > 2 else ""
                    messagebox.showinfo("Animation ready", f"Animation equations and the DesmosPlayer program are saved.\n\nAnimation data:\n{output}\n\nDesmosPlayer program ({program_size_mb:.1f} MB):\n{player_output}\n\nFor Desmos.com in Chrome/Edge: install/enable the DesmosPlayer userscript, choose + → Program, paste the .js loader, and click its round icon. When the file picker opens, select the matching animation JSON in this folder. Then click DesmosPlayer's Start button; f tracks the frame.{size_note}")
                elif event == "cpu_render_progress":
                    done, total = payload
                    self.progress.configure(mode="determinate", maximum=max(1, total), value=done)
                    self.status.configure(text=f"CPU rendering frame {done} of {total} across multiple cores…")
                elif event == "cpu_rendered":
                    self._busy(False)
                    info, animation_json, equation_frames = payload
                    self.video_output = Path(info["path"])
                    self.video_animation_json = Path(animation_json)
                    self.video_equation_frames = equation_frames
                    self.video_equation_cache_dir = None
                    self.video_render_kind = "jsxgraph-cpu"
                    self.video_fps = float(info["fps"])
                    self.video_frame_count = int(info["frames"])
                    self.video_equations_btn.configure(state="normal")
                    self.load_mp4_btn.configure(state="normal")
                    self.open_output_kind = "mp4"
                    self.open_btn.configure(text="Open MP4 folder", state="normal")
                    self.status.configure(text=f"CPU MP4 saved · {info['width']}×{info['height']} · {info['workers']} CPU workers · {info['codec']}")
                    self._append_log(f"CPU render complete · {info['frames']} frames at {info['fps']:g} FPS · {info['path']}")
                    self._open_video_equations()
                elif event == "stream_started":
                    self.job_dir = payload
                    self.open_output_kind = "mp4"
                    self.open_btn.configure(text="Open MP4 folder")
                    self.open_btn.configure(state="normal")
                    self._busy(True)
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(1, self.stream_frame_total), value=0)
                    self.status.configure(text=f"Rendering 0 of {self.stream_frame_total} source frames in Desmos…")
                    self.log_box.configure(state="normal")
                    self.log_box.delete("1.0", "end")
                    self.log_box.configure(state="disabled")
                    self._append_log(f"Render started · {self.stream_frame_total} frames at {self.stream_source_fps:.2f} FPS")
                elif event == "stream_frame_read":
                    index, total = payload
                    self.status.configure(text=f"Desmos is drawing frame {index + 1} of {total}…")
                    self._append_log(f"Frame {index + 1:04d}/{total:04d} · source frame ready; waiting for Desmos")
                elif event == "stream_frame_saved":
                    count, total = payload
                    self.progress.configure(mode="determinate", maximum=max(1, total), value=count)
                    self.status.configure(text=f"Captured {count} of {total} frames from Desmos…")
                    self._append_log(f"Frame {count:04d}/{total:04d} · captured to MP4")
                elif event == "stream_animation_saved":
                    json_path, frame_count, fps, cache_dir = payload
                    self.video_animation_json = Path(json_path)
                    self.video_equation_cache_dir = Path(cache_dir)
                    self.video_equation_frames = [{"time": i / max(float(fps), 1.0)} for i in range(int(frame_count))]
                    self.video_equations_btn.configure(state="normal")
                    self.status.configure(text=f"Player JSON saved · smooth MP4 replay is ready")
                    self._append_log(f"Player JSON saved · {json_path}")
                elif event == "recorded":
                    self._busy(False)
                    self.video_output = Path(payload)
                    if self.video_animation_json and self.video_animation_json.is_file():
                        self.video_equations_btn.configure(state="normal")
                    self.status.configure(text=f"MP4 saved: {self.video_output}")
                    self.open_output_kind = "mp4"
                    self.open_btn.configure(text="Open MP4 folder", state="normal")
                    self.load_mp4_btn.configure(state="normal")
                    self._append_log(f"Render complete · saved {self.video_output}")
                elif event == "error":
                    self._busy(False)
                    self.status.configure(text="Something went wrong. See the message for details.")
                    self._append_log(f"ERROR · {payload}")
                    messagebox.showerror("Processing error", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)


    def _streamer_html(self) -> str:
        if not self.api_key:
            raise ValueError("Set DESMOS_API_KEY in the VS Code terminal, then restart the desktop app.")
        try:
            clip_seconds = float(self.seconds.get())
        except (TypeError, ValueError):
            raise ValueError("Clip seconds must be a number between 1 and 20.")
        if not 1 <= clip_seconds <= 20:
            raise ValueError("Clip seconds must be between 1 and 20.")
        api_url = "https://www.desmos.com/api/v1.12/calculator.js?apiKey=" + quote(self.api_key, safe="")
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Desmos Graphing Calculator · Anime Studio</title><script src="{html.escape(api_url, quote=True)}"></script><style>body{{margin:0;background:#fff;color:#20242b;font:14px Arial,sans-serif}}#bar{{height:40px;box-sizing:border-box;position:relative;display:flex;align-items:center;gap:8px;padding:0 9px;background:#202020;color:#fff;border-bottom:1px solid #111}}body.presentation #bar .actions{{display:none}}.doc-title{{height:25px;display:flex;align-items:center;gap:8px;padding:0 8px;background:#f7f7f7;color:#222;border:1px solid #aaa;border-radius:2px;font-size:11px;white-space:nowrap}}.doc-title:after{{content:'⌄';font-size:10px;color:#555}}.brand{{position:absolute;left:50%;transform:translateX(-50%);height:100%;display:flex;align-items:center;gap:5px;white-space:nowrap;color:#f6f6f6}}.brand b{{font-size:16px;font-weight:500;letter-spacing:-.5px}}.brand .mark,.brand i,.brand span:last-child{{display:none}}.actions{{margin-left:auto;display:flex;align-items:center;gap:5px;min-width:0}}button{{font:12px Arial,sans-serif;padding:5px 8px;background:#333;color:#eee;border:1px solid #555;border-radius:2px;cursor:pointer;white-space:nowrap}}button:hover{{background:#484848}}button.primary{{background:#4088d7;color:white;border-color:#4088d7;font-weight:600}}button.primary:hover{{background:#3478c3}}button:disabled{{opacity:.55;cursor:default}}#status{{max-width:215px;color:#ddd;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}#progress{{width:82px;height:7px;accent-color:#4b9cff}}#calc{{height:calc(100vh - 40px)}}#player{{display:none;width:100vw;height:100vh;background:#05070b}}#player video{{display:block;width:100%;height:100%;object-fit:contain;background:#05070b}}@media(max-width:900px){{#status{{display:none}}#progress{{width:58px}}}}</style></head><body><div id="bar"><div class="doc-title">Anime to Desmos</div><div class="brand"><b>desmos</b></div><div class="actions"><button id="start" class="primary">Render full-FPS</button><button id="stop" disabled>Stop</button><button id="toggleEquations">Hide equations</button><button id="fullscreen">Full screen</button><progress id="progress" max="100" value="0"></progress><span id="status">Renders every source frame and records the Desmos output. This can take time.</span></div></div><div id="calc"></div><script>
const clipSeconds={clip_seconds};
const calc=Desmos.GraphingCalculator(document.getElementById('calc'),{{expressions:true,expressionsCollapsed:false,settingsMenu:true,zoomButtons:true,expressionsTopbar:true,lockViewport:false,images:true,folders:true,notes:true,sliders:true,keypad:true,accentColor:'#2f72dc'}});
const status=document.getElementById('status'),start=document.getElementById('start'),stop=document.getElementById('stop'),progress=document.getElementById('progress'),toggleEquations=document.getElementById('toggleEquations');let cancelled=false,active=false,previous=0,equationsVisible=true,playbackReady=false,lastPlaybackIndex=-1,playbackRequest=0,totalFrames=0;
document.getElementById('fullscreen').addEventListener('click',async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}}catch(error){{status.textContent='Full screen is unavailable in this browser.';}}}});document.addEventListener('fullscreenchange',()=>document.body.classList.toggle('presentation',Boolean(document.fullscreenElement)));
const fHelper=calc.HelperExpression({{latex:'f'}});fHelper.observe('numericValue',()=>{{const v=fHelper.numericValue;if(playbackReady&&Number.isFinite(v)){{const n=Math.max(0,Math.min(totalFrames-1,Math.round(v)));if(n!==lastPlaybackIndex)drawPlaybackFrame(n).catch(e=>status.textContent=e.message);}}}});
toggleEquations.addEventListener('click',()=>{{equationsVisible=!equationsVisible;calc.updateSettings({{expressionsCollapsed:!equationsVisible}});toggleEquations.textContent=equationsVisible?'Hide equations':'Show equations';}});
async function request(path,data,method='POST'){{const options={{method,headers:{{'Content-Type':'application/json'}}}};if(method==='POST')options.body=JSON.stringify(data||{{}});const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw new Error(result.error||'Render failed');return result;}}
function screenshot(){{return new Promise((resolve,reject)=>calc.asyncScreenshot(image=>image?resolve(image):reject(new Error('Desmos screenshot was empty.'))));}}
async function drawPlaybackFrame(index){{const token=++playbackRequest;const data=await request(`/stream/points?index=${{index}}`,null,'GET');if(token!==playbackRequest||!playbackReady)return;const expr=data.points.map((p,i)=>({{id:`stream_points_${{i}}`,latex:`([${{p.x.join(',')}}],[${{p.y.join(',')}}])`,color:'#111111',pointSize:2,pointOpacity:1,lines:false,lineWidth:0}}));const gone=[];for(let i=expr.length;i<previous;i++)gone.push({{id:`stream_points_${{i}}`}});if(gone.length)calc.removeExpressions(gone);previous=expr.length;if(expr.length)calc.setExpressions(expr);lastPlaybackIndex=index;}}
start.addEventListener('click',async()=>{{if(active)return;active=true;playbackReady=false;lastPlaybackIndex=-1;cancelled=false;progress.value=0;start.disabled=true;stop.disabled=false;if(equationsVisible){{equationsVisible=false;calc.updateSettings({{expressionsCollapsed:true}});toggleEquations.textContent='Show equations';}}try{{const meta=await request('/stream/start',{{seconds:clipSeconds}});totalFrames=meta.total;status.textContent=`Preparing ${{meta.total}} frames (${{meta.seconds}}s) at ${{meta.fps.toFixed(2)}} FPS…`;while(!cancelled){{const frame=await request('/stream/next',null,'GET');if(frame.done)break;progress.value=Math.round(((frame.index+1)/frame.total)*100);status.textContent=`Frame ${{frame.index+1}}/${{frame.total}} · plotting equations…`;await new Promise(resolve=>requestAnimationFrame(()=>resolve()));const expressions=[{{id:'animation_f',latex:`f=${{frame.index}}`,sliderBounds:{{min:'0',max:String(frame.total-1),step:'1'}},playing:false}},...frame.points.map((part,i)=>({{id:`stream_points_${{i}}`,latex:`([${{part.x.join(',')}}],[${{part.y.join(',')}}])`,color:'#111111',pointSize:2,pointOpacity:1,lines:false,lineWidth:0}}))];const remove=[];for(let i=frame.points.length;i<previous;i++)remove.push({{id:`stream_points_${{i}}`}});if(remove.length)calc.removeExpressions(remove);previous=frame.points.length;calc.setExpressions(expressions);calc.setMathBounds({{left:0,right:frame.width,bottom:0,top:frame.height}});status.textContent=`Frame ${{frame.index+1}}/${{frame.total}} · capturing Desmos…`;const image=await screenshot();await request('/stream/frame',{{image}});}}status.textContent='Finalizing MP4 and player JSON…';const result=await request('/stream/finish',{{}});if(!result.animation_json)throw new Error(result.animation_json_error||'The MP4 rendered, but its single-file equation export could not be created.');status.textContent='Render complete · opening smooth MP4 replay…';window.location.replace('/video-equations');}}catch(error){{try{{await request('/stream/finish',{{}});}}catch(_e){{}}status.textContent=error.message;}}finally{{active=false;cancelled=false;start.disabled=false;stop.disabled=true;}}}});
stop.addEventListener('click',()=>{{cancelled=true;stop.disabled=true;status.textContent='Finishing the current rendered frame…';}});
</script></body></html>'''

    def _open_app_window(self, url: str):
        """Open the local calculator in an app-style Edge/Chrome window without browser chrome."""
        candidates = [shutil.which("msedge"), shutil.which("msedge.exe"), shutil.which("chrome"), shutil.which("chrome.exe")]
        for variable, relative in (
            ("PROGRAMFILES(X86)", Path("Microsoft/Edge/Application/msedge.exe")),
            ("PROGRAMFILES", Path("Microsoft/Edge/Application/msedge.exe")),
            ("LOCALAPPDATA", Path("Microsoft/Edge/Application/msedge.exe")),
            ("PROGRAMFILES(X86)", Path("Google/Chrome/Application/chrome.exe")),
            ("PROGRAMFILES", Path("Google/Chrome/Application/chrome.exe")),
            ("LOCALAPPDATA", Path("Google/Chrome/Application/chrome.exe")),
        ):
            base = os.environ.get(variable)
            if base:
                candidates.append(str(Path(base) / relative))
        executable = next((Path(item) for item in candidates if item and Path(item).is_file()), None)
        if executable is None:
            raise RuntimeError("Microsoft Edge or Google Chrome is required to show Desmos in its standalone app window.")
        subprocess.Popen([str(executable), f"--app={url}", "--new-window"], cwd=str(ROOT))

    def _open_streamer(self):
        if not self.video_path:
            messagebox.showinfo("Choose a video", "Choose a source video first.")
            return
        if self.mode.get() != "sketch":
            messagebox.showinfo("Choose Sketch mode", "Full-FPS recording uses the lightweight Sketch renderer. Select Sketch mode first.")
            return
        try:
            if not self.api_key:
                raise ValueError("Set DESMOS_API_KEY in the VS Code terminal, then restart the desktop app.")
            self._ensure_viewer_server()
            self._open_app_window(f"http://127.0.0.1:{self.viewer_server.server_port}/streamer")
        except Exception as exc:
            messagebox.showerror("Could not open full-FPS renderer", str(exc))

    def _viewer_html(self) -> str:
        if not self.animation or not self.api_key:
            raise ValueError("Set DESMOS_API_KEY in the VS Code terminal, then restart the desktop app.")
        frame_count = len(self.animation)
        api_url = "https://www.desmos.com/api/v1.12/calculator.js?apiKey=" + quote(self.api_key, safe="")
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Desmos Graphing Calculator · Anime Studio</title><script src="{html.escape(api_url, quote=True)}"></script><style>body{{margin:0;background:#fff;color:#20242b;font:14px Arial,sans-serif}}#bar{{height:40px;box-sizing:border-box;position:relative;display:flex;align-items:center;gap:7px;padding:0 9px;background:#202020;color:#fff;border-bottom:1px solid #111}}body.presentation #bar .tools{{display:none}}.doc-title{{height:25px;display:flex;align-items:center;gap:8px;padding:0 8px;background:#f7f7f7;color:#222;border:1px solid #aaa;border-radius:2px;font-size:11px;white-space:nowrap}}.doc-title:after{{content:'⌄';font-size:10px;color:#555}}.brand{{position:absolute;left:50%;transform:translateX(-50%);height:100%;display:flex;align-items:center;gap:5px;white-space:nowrap;color:#f6f6f6}}.brand b{{font-size:16px;font-weight:500;letter-spacing:-.5px}}.brand .mark,.brand i,.brand span:last-child{{display:none}}.tools{{margin-left:auto;display:flex;align-items:center;gap:6px;min-width:0}}button,input{{font:12px Arial,sans-serif}}button{{padding:5px 8px;background:#333;color:#eee;border:1px solid #555;border-radius:2px;cursor:pointer;white-space:nowrap}}button:hover{{background:#484848}}button.primary{{background:#4088d7;color:white;border-color:#4088d7;font-weight:600}}button.primary:hover{{background:#3478c3}}#timeline{{width:145px;min-width:65px;accent-color:#4b9cff}}#time{{color:#ddd;font-size:11px;white-space:nowrap}}#calc{{height:calc(100vh - 40px)}}@media(max-width:800px){{#time{{display:none}}#timeline{{width:80px}}}}</style></head><body><div id="bar"><div class="doc-title">Anime to Desmos</div><div class="brand"><b>desmos</b></div><div class="tools"><button id="play" class="primary">Play</button><button id="record">Record MP4</button><button id="fullscreen">Full screen</button><input id="timeline" type="range" min="0" max="0" value="0"><span id="time">Waiting for animation</span></div></div><div id="calc"></div><script>
document.getElementById('fullscreen').addEventListener('click',async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}}catch(_e){{}}}});document.addEventListener('fullscreenchange',()=>document.body.classList.toggle('presentation',Boolean(document.fullscreenElement)));
const frameCount={frame_count};
const fps={self.sample_fps};
const calc=Desmos.GraphingCalculator(document.getElementById('calc'),{{expressions:true,settingsMenu:true,zoomButtons:true,expressionsTopbar:true,lockViewport:false,images:true,folders:true,notes:true,sliders:true,keypad:true,accentColor:'#2f72dc'}});
let index=0,timer=null,helper=null,renderTicket=0,playbackTicket=0,previousCount=0;
const frameCache=new Map();
const range=document.getElementById('timeline'),label=document.getElementById('time'),play=document.getElementById('play');
range.max=String(frameCount-1);
async function fetchFrame(i){{if(frameCache.has(i))return frameCache.get(i);const response=await fetch(`/animation/frame?index=${{i}}`);if(!response.ok)throw new Error('Could not load frame equations '+(i+1));const frame=await response.json();frameCache.set(i,frame);if(frameCache.size>12){{const oldest=frameCache.keys().next().value;if(oldest!==index)frameCache.delete(oldest);}}return frame;}}
async function render(i,updateSlider=true){{index=Math.max(0,Math.min(frameCount-1,i));if(updateSlider)range.value=String(index);const ticket=++renderTicket;label.textContent=`Loading · frame ${{index+1}}/${{frameCount}} · f=${{index}}`;try{{const frame=await fetchFrame(index);if(ticket!==renderTicket)return;label.textContent=`${{frame.time.toFixed(2)}} sec · frame ${{index+1}}/${{frameCount}} · f=${{index}}`;const expr=[{{id:'animation_f',latex:`f=${{index}}`,sliderBounds:{{min:'0',max:String(frameCount-1),step:'1'}},playing:false}},...frame.expressions.map((e,j)=>({{id:`frame_${{j}}`,latex:e.latex,color:e.color,lineWidth:e.lineWidth,fill:e.fill,fillOpacity:e.fillOpacity,lineOpacity:e.lineOpacity,hidden:false}}))];const removals=[];for(let j=frame.expressions.length;j<previousCount;j++)removals.push({{id:`frame_${{j}}`}});if(removals.length)calc.removeExpressions(removals);previousCount=frame.expressions.length;calc.setExpressions(expr);calc.setMathBounds(frame.graph_bounds);}}catch(error){{if(ticket===renderTicket)label.textContent=error.message;}}}}
async function advance(ticket){{if(timer!==ticket)return;await render((index+1)%frameCount);if(timer===ticket)setTimeout(()=>advance(ticket),1000/fps);}}
render(0);
helper=calc.HelperExpression({{latex:'f'}});helper.observe('numericValue',()=>{{const value=helper.numericValue;if(Number.isFinite(value))render(Math.round(value));}});
range.addEventListener('input',()=>{{pause();render(Number(range.value));}});
function pause(){{timer=null;playbackTicket++;play.textContent='Play';}}
play.addEventListener('click',()=>{{if(timer!==null){{pause();return;}}play.textContent='Pause';timer=++playbackTicket;advance(timer);}});
const record=document.getElementById('record');let recording=false,stopRequested=false;
async function post(path,data={{}}){{const response=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});const result=await response.json();if(!response.ok)throw new Error(result.error||'Recording failed');return result;}}
record.addEventListener('click',async()=>{{if(recording){{stopRequested=true;record.textContent='Stopping…';return;}}recording=true;stopRequested=false;pause();record.disabled=false;record.textContent='Stop recording';try{{await post('/record/start',{{fps}});for(let i=0;i<frameCount;i++){{if(stopRequested)break;await render(i);await new Promise((resolve,reject)=>calc.asyncScreenshot(async(image)=>{{try{{await post('/record/frame',{{image}});resolve();}}catch(error){{reject(error);}}}}));label.textContent=`Recording ${{i+1}}/${{frameCount}} · f=${{i}}`;}}const done=await post('/record/finish');label.textContent=`Saved ${{done.frames}} frames · ${{done.file}}`;}}catch(error){{try{{await post('/record/finish');}}catch(_e){{}}label.textContent=error.message;}}finally{{recording=false;stopRequested=false;record.textContent='Record MP4';}}}});
</script></body></html>'''

    def _jsxgraph_html(self) -> str:
        if not self.animation:
            raise ValueError("Build an animation first so the JSXGraph preview has frame data.")
        frame_count = len(self.animation)
        fps = max(1.0, min(float(self.sample_fps), 30.0))
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Anime to JSXGraph</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/jsxgraph@1.14.0/distrib/jsxgraph.css"><script src="https://cdn.jsdelivr.net/npm/jsxgraph@1.14.0/distrib/jsxgraphcore.js"></script><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;font:13px Arial,sans-serif;color:#202124;background:#fff;overflow:hidden}}#top{{height:42px;background:#202124;color:#f4f4f4;display:flex;align-items:center;gap:10px;padding:0 12px;border-bottom:1px solid #111}}.brand{{font-size:15px;font-weight:600;letter-spacing:-.3px}}.tag{{color:#aaa;font-size:11px}}.tools{{margin-left:auto;display:flex;align-items:center;gap:8px}}button{{border:1px solid #555;background:#333;color:#eee;border-radius:3px;padding:6px 10px;cursor:pointer}}button.primary{{background:#2676cf;border-color:#2676cf}}button:hover{{filter:brightness(1.15)}}#timeline{{width:min(24vw,260px)}}#clock{{min-width:100px;color:#ddd;font-variant-numeric:tabular-nums}}#layout{{height:calc(100% - 42px);min-height:0;display:grid;grid-template-rows:minmax(0,1fr);grid-template-columns:310px minmax(0,1fr)}}#side{{border-right:1px solid #c9c9c9;background:#fff;min-width:0;display:flex;flex-direction:column}}#sidehead{{height:36px;background:#f7f7f7;border-bottom:1px solid #ddd;display:flex;align-items:center;padding:0 10px;font-weight:600}}#equations{{height:100%;overflow:auto;position:relative;font:12px Georgia,serif}}.row{{height:30px;display:flex;align-items:center;gap:8px;padding:0 8px;border-bottom:1px solid #eee;white-space:nowrap;overflow:hidden}}.swatch{{width:12px;height:12px;border:1px solid #777;flex:none}}.latex{{overflow:hidden;text-overflow:ellipsis;color:#222}}.muted{{color:#777;font:11px Arial,sans-serif;padding:12px}}#stage{{min-width:0;min-height:0;position:relative;background:white}}#jxgbox{{width:100%;height:100%;border:0;touch-action:none}}.jxgbox{{border:0}}body.presentation #top,body.presentation #side{{display:none}}body.presentation #layout{{height:100%;display:block}}body.presentation #stage{{width:100%;height:100%}}#error{{position:absolute;inset:0;display:none;place-items:center;color:#6b2020;background:#fff;padding:30px;text-align:center}}@media(max-width:720px){{#layout{{grid-template-columns:240px 1fr}}.tag{{display:none}}#side{{width:240px}}}}
</style></head><body><header id="top"><div class="brand">jsxgraph</div><div class="tag">Anime to Desmos · CPU MP4 render from Studio</div><div class="tools"><button id="play" class="primary">Play</button><button id="equationToggle">Hide equations</button><button id="fullscreen">Full screen</button><input id="timeline" type="range" min="0" max="{frame_count-1}" step="1" value="0"><span id="clock">Frame 1 / {frame_count}</span></div></header><main id="layout"><aside id="side"><div id="sidehead">Expressions <span id="count" style="margin-left:auto;color:#777;font-weight:400"></span></div><div id="equations"><div class="muted">Loading equations…</div></div></aside><section id="stage"><div id="jxgbox" class="jxgbox"></div><div id="error"></div></section></main><script>
(()=>{{
const frameCount={frame_count},fps={fps:.6g},cache=new Map(),colors=[];
const box=document.getElementById('jxgbox'),timeline=document.getElementById('timeline'),clock=document.getElementById('clock'),rows=document.getElementById('equations'),count=document.getElementById('count'),error=document.getElementById('error'),play=document.getElementById('play');
let index=0,timer=null,token=0,art=[];
if(!window.JXG){{error.textContent='JSXGraph could not load. Check the internet connection and refresh.';error.style.display='grid';return;}}
const board=JXG.JSXGraph.initBoard('jxgbox',{{boundingbox:[0,1,1,0],axis:true,grid:true,keepaspectratio:true,showNavigation:false,showCopyright:false,renderer:'canvas',pan:{{enabled:false}},zoom:{{enabled:false}},defaultAxes:{{x:{{ticks:{{visible:true}}}},y:{{ticks:{{visible:true}}}}}}}});
async function frame(i){{if(cache.has(i))return cache.get(i);const response=await fetch(`/animation/frame?index=${{i}}`);if(!response.ok)throw new Error('Could not load animation frame '+(i+1));const data=await response.json();cache.set(i,data);if(cache.size>5){{const first=cache.keys().next().value;if(first!==index)cache.delete(first);}}return data;}}
function showEquations(expressions){{rows.innerHTML='';count.textContent=`${{expressions.length.toLocaleString()}}`;const rowHeight=30;function paint(){{const first=Math.max(0,Math.floor(rows.scrollTop/rowHeight)-3),visible=Math.ceil(rows.clientHeight/rowHeight)+7,last=Math.min(expressions.length,first+visible);rows.innerHTML=`<div style="height:${{first*rowHeight}}px"></div>`;for(let i=first;i<last;i++){{const e=expressions[i],row=document.createElement('div');row.className='row';const swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=e.color||'#111';const code=document.createElement('span');code.className='latex';code.textContent=e.latex||'';code.title=e.latex||'';row.append(swatch,code);rows.appendChild(row);}}const bottom=Math.max(0,(expressions.length-last)*rowHeight);rows.insertAdjacentHTML('beforeend',`<div style="height:${{bottom}}px"></div>`);}}rows.onscroll=paint;paint();}}
async function render(i){{const thisToken=++token;index=Math.max(0,Math.min(frameCount-1,i));timeline.value=String(index);clock.textContent=`Frame ${{index+1}} / ${{frameCount}}`;error.style.display='none';try{{const data=await frame(index);if(thisToken!==token)return;const expressions=data.expressions||[];if(expressions.some(e=>!e.geometry||!Array.isArray(e.geometry.points)))throw new Error('This animation was made before JSXGraph support. Rebuild the animation once in the updated app.');board.suspendUpdate();try{{if(art.length)board.removeObject(art,false);art=[];const b=data.graph_bounds||{{left:0,right:data.image_width||1,bottom:0,top:data.image_height||1}};board.setBoundingBox([b.left,b.top,b.right,b.bottom],false);for(const e of expressions){{const g=e.geometry,pts=g.points||[];if(g.type==='polygon'&&pts.length>=3){{art.push(board.create('polygon',pts,{{withLines:false,fillColor:e.color||'#222',fillOpacity:e.fillOpacity??1,vertices:{{visible:false}},borders:{{visible:false,strokeOpacity:0,strokeWidth:0}}}}));}}else if(g.type==='curve'&&pts.length>=2){{art.push(board.create('curve',[pts.map(p=>p[0]),pts.map(p=>p[1])],{{strokeColor:e.color||'#111',strokeWidth:e.lineWidth||1.5,strokeOpacity:1,highlight:false}}));}}}}}}finally{{board.unsuspendUpdate();}}showEquations(expressions);}}catch(e){{if(thisToken===token){{error.textContent=e.message;error.style.display='grid';}}}}}}
function pause(){{timer=null;play.textContent='Play';}}
function advance(id){{if(timer!==id)return;const started=performance.now();render((index+1)%frameCount).finally(()=>{{if(timer===id)setTimeout(()=>advance(id),Math.max(0,1000/fps-(performance.now()-started)));}});}}
play.onclick=()=>{{if(timer!==null){{pause();return;}}play.textContent='Pause';timer=Date.now()+Math.random();advance(timer);}};
timeline.oninput=()=>{{pause();render(Number(timeline.value));}};
document.getElementById('fullscreen').onclick=async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}}catch(_e){{}}}};
document.addEventListener('fullscreenchange',()=>document.body.classList.toggle('presentation',Boolean(document.fullscreenElement)));
document.getElementById('equationToggle').onclick=()=>{{const hidden=document.body.classList.toggle('noeq');document.getElementById('side').style.display=hidden?'none':'';document.getElementById('layout').style.gridTemplateColumns=hidden?'1fr':'310px minmax(0,1fr)';document.getElementById('equationToggle').textContent=hidden?'Show equations':'Hide equations';}};
render(0);
}})();</script></body></html>'''

    def _video_equations_html(self) -> str:
        if not self.video_output or not self.video_output.is_file():
            raise ValueError("Record the animation to MP4 first.")
        if not self.video_animation_json or not self.video_animation_json.is_file():
            raise ValueError("This MP4 is not paired with the generated equation animation. Record it from the Desmos animation page first.")
        if not DESMOS_SKIN_PATH.is_file():
            raise ValueError("The Desmos UI PNG skin is missing from the project assets folder.")
        frames = self.video_equation_frames
        if isinstance(frames, list) and frames:
            frame_times = [float(frame.get("time", i / max(self.video_fps, 1.0))) for i, frame in enumerate(frames)]
        else:
            raise ValueError("The matching equation frame data is not available. Render the MP4 again from the app.")
        safe_times = json.dumps(frame_times, separators=(",", ":"))
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Anime-to-Desmos · Video preview</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#fff;font-family:Arial,sans-serif}}#skin{{position:relative;width:100vw;height:100vh;background:#fff url('/desmos-skin.png') center center/100% 100% no-repeat}}#equations{{position:absolute;left:.4%;top:8.6%;width:24.3%;height:86%;overflow:auto;background:#fff;border-right:1px solid #bbb;box-shadow:1px 0 2px #999;z-index:2;font:clamp(8px,.78vw,11px) Arial,sans-serif}}#equation-count{{position:sticky;top:0;height:19px;padding:3px 5px;background:#f7f7f7;border-bottom:1px solid #ddd;color:#555;font-size:9px;z-index:1}}.row{{height:25px;display:flex;align-items:center;gap:5px;padding:0 4px;border-bottom:1px solid #eee;white-space:nowrap;overflow:hidden}}.icon{{width:15px;height:15px;flex:none;border-radius:50%;background:#171717;position:relative}}.icon:after{{content:'';position:absolute;left:3px;top:4px;width:9px;height:6px;border-top:1.4px solid white;border-radius:50%;transform:rotate(-15deg)}}.swatch{{width:8px;height:8px;border:1px solid #777;flex:none}}.latex{{color:#202124;overflow:hidden;text-overflow:ellipsis}}#video{{position:absolute;left:25.15%;right:2.45%;top:8.55%;bottom:5.3%;width:72.4%;height:86.15%;object-fit:contain;background:#fff;z-index:1}}#disclosure{{position:absolute;left:3.8%;top:.7%;z-index:5;padding:2px 5px;border:1px solid #999;background:#fff;color:#333;font-size:clamp(7px,.85vw,10px);white-space:nowrap}}#fullscreen{{position:absolute;right:.4%;top:1.8%;z-index:5;width:24px;height:24px;border:1px solid #bbb;border-radius:3px;background:#f7f7f7;color:#333;cursor:pointer;font-size:16px;line-height:17px;padding:0}}#fullscreen:hover{{background:#e7e7e7}}body.presentation #disclosure,body.presentation #fullscreen{{display:none}}body.presentation #skin{{width:100vw;height:100vh}}@media(max-width:600px){{#equations{{font-size:8px}}.row{{height:22px;gap:3px;padding:0 2px}}}}
</style></head><body><div id="skin"><div id="disclosure">PRE-RENDERED VIDEO · MATCHED GENERATED EQUATIONS</div><button id="fullscreen" title="Full screen">⛶</button><aside id="equations"><div id="equation-count">Expressions</div></aside><video id="video" controls autoplay muted playsinline preload="auto" src="/rendered-video"></video></div><script>
const frameTimes={safe_times};const video=document.getElementById('video'),list=document.getElementById('equations'),count=document.getElementById('equation-count');let activeFrame=-1,requestId=0;
function drawRows(items){{const rowHeight=25;function paint(){{const first=Math.max(0,Math.floor((list.scrollTop-19)/rowHeight)-3),visible=Math.ceil(list.clientHeight/rowHeight)+7,last=Math.min(items.length,first+visible);list.innerHTML=`<div id="equation-count" style="height:19px">Frame equations · ${{items.length.toLocaleString()}}</div><div style="height:${{first*rowHeight}}px"></div>`;for(let i=first;i<last;i++){{const e=items[i],row=document.createElement('div');row.className='row';const icon=document.createElement('span');icon.className='icon';const color=document.createElement('span');color.className='swatch';color.style.background=e.color||'#111';const latex=document.createElement('span');latex.className='latex';latex.textContent=e.latex||'';latex.title=e.latex||'';row.append(icon,color,latex);list.appendChild(row);}}list.insertAdjacentHTML('beforeend',`<div style="height:${{Math.max(0,(items.length-last)*rowHeight)}}px"></div>`);}}list.onscroll=paint;paint();}}
async function syncEquations(){{if(!frameTimes.length)return;const time=Number(video.currentTime)||0;let best=0,diff=Infinity;for(let i=0;i<frameTimes.length;i++){{const d=Math.abs(frameTimes[i]-time);if(d<diff){{best=i;diff=d;}}}}if(best===activeFrame)return;activeFrame=best;const ticket=++requestId;try{{const response=await fetch(`/video-equations/frame?index=${{best}}`);if(!response.ok)throw new Error('Could not load the matching equation frame.');const data=await response.json();if(ticket===requestId)drawRows(data.expressions||[]);}}catch(error){{if(ticket===requestId){{list.innerHTML=`<div style="padding:10px;color:#a22">${{error.message}}</div>`;}}}}}}
video.addEventListener('timeupdate',syncEquations);video.addEventListener('seeked',syncEquations);video.addEventListener('loadedmetadata',syncEquations);document.getElementById('fullscreen').onclick=async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}}catch(_e){{}}}};document.addEventListener('fullscreenchange',()=>document.body.classList.toggle('presentation',Boolean(document.fullscreenElement)));syncEquations();
</script></body></html>'''

    def _jsxgraph_video_html(self) -> str:
        if not self.video_output or not self.video_output.is_file():
            raise ValueError("Record a JSXGraph animation to MP4 first.")
        if not isinstance(self.video_equation_frames, list) or not self.video_equation_frames:
            raise ValueError("The matching JSXGraph equation frames are unavailable. Render the animation again.")
        frame_times = [float(frame.get("time", i / max(self.video_fps, 1.0))) for i, frame in enumerate(self.video_equation_frames)]
        safe_times = json.dumps(frame_times, separators=(",", ":"))
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Anime to JSXGraph · Rendered video</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#fff;font:13px Arial,sans-serif;color:#202124}}#top{{height:42px;background:#202124;color:#f4f4f4;display:flex;align-items:center;gap:10px;padding:0 12px;border-bottom:1px solid #111}}.brand{{font-size:15px;font-weight:600}}.tag{{color:#aaa;font-size:11px}}#fullscreen{{margin-left:auto;border:1px solid #555;background:#333;color:#eee;border-radius:3px;padding:6px 10px;cursor:pointer}}#layout{{height:calc(100% - 42px);min-height:0;display:grid;grid-template-rows:minmax(0,1fr);grid-template-columns:310px minmax(0,1fr)}}#side{{border-right:1px solid #c9c9c9;min-width:0;display:flex;flex-direction:column}}#sidehead{{height:36px;background:#f7f7f7;border-bottom:1px solid #ddd;display:flex;align-items:center;padding:0 10px;font-weight:600}}#equations{{height:100%;overflow:auto;font:12px Georgia,serif}}.row{{height:30px;display:flex;align-items:center;gap:8px;padding:0 8px;border-bottom:1px solid #eee;white-space:nowrap;overflow:hidden}}.swatch{{width:12px;height:12px;border:1px solid #777;flex:none}}.latex{{overflow:hidden;text-overflow:ellipsis}}#stage{{min-width:0;min-height:0;height:100%;position:relative;background:#fff;display:grid;place-items:center}}#video{{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;background:#fff}}body.presentation #top,body.presentation #side{{display:none}}body.presentation #layout{{height:100%;display:block}}body.presentation #stage{{width:100%;height:100%}}@media(max-width:720px){{#layout{{grid-template-columns:240px 1fr}}.tag{{display:none}}}}
</style></head><body><header id="top"><div class="brand">jsxgraph</div><div class="tag">Rendered MP4 · equations follow playback</div><button id="fullscreen">Full screen</button></header><main id="layout"><aside id="side"><div id="sidehead">Expressions <span id="count" style="margin-left:auto;color:#777;font-weight:400"></span></div><div id="equations"></div></aside><section id="stage"><video id="video" controls autoplay muted playsinline preload="auto" src="/rendered-video"></video></section></main><script>
const frameTimes={safe_times};const video=document.getElementById('video'),rows=document.getElementById('equations'),count=document.getElementById('count');let activeFrame=-1,requestId=0;
function draw(items){{count.textContent=`${{items.length.toLocaleString()}}`;const rowHeight=30;function paint(){{const first=Math.max(0,Math.floor(rows.scrollTop/rowHeight)-3),visible=Math.ceil(rows.clientHeight/rowHeight)+7,last=Math.min(items.length,first+visible);rows.innerHTML=`<div style="height:${{first*rowHeight}}px"></div>`;for(let i=first;i<last;i++){{const e=items[i],row=document.createElement('div');row.className='row';const swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=e.color||'#111';const latex=document.createElement('span');latex.className='latex';latex.textContent=e.latex||'';latex.title=e.latex||'';row.append(swatch,latex);rows.appendChild(row);}}rows.insertAdjacentHTML('beforeend',`<div style="height:${{Math.max(0,(items.length-last)*rowHeight)}}px"></div>`);}}rows.onscroll=paint;paint();}}
async function sync(){{if(!frameTimes.length)return;const time=Number(video.currentTime)||0;let best=0,diff=Infinity;for(let i=0;i<frameTimes.length;i++){{const d=Math.abs(frameTimes[i]-time);if(d<diff){{best=i;diff=d;}}}}if(best===activeFrame)return;activeFrame=best;const ticket=++requestId;try{{const response=await fetch(`/video-equations/frame?index=${{best}}`);if(!response.ok)throw new Error('Could not load the matching equation frame.');const data=await response.json();if(ticket===requestId)draw(data.expressions||[]);}}catch(error){{if(ticket===requestId)rows.textContent=error.message;}}}}
video.addEventListener('timeupdate',sync);video.addEventListener('seeked',sync);video.addEventListener('loadedmetadata',()=>{{sync();video.play().catch(()=>{{}});}});document.getElementById('fullscreen').onclick=async()=>{{try{{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}}catch(_e){{}}}};document.addEventListener('fullscreenchange',()=>document.body.classList.toggle('presentation',Boolean(document.fullscreenElement)));sync();
</script></body></html>'''

    def _open_video_equations(self):
        try:
            jsxgraph_video = self.video_render_kind.startswith("jsxgraph")
            route = "/jsxgraph-video" if jsxgraph_video else "/video-equations"
            (self._jsxgraph_video_html() if jsxgraph_video else self._video_equations_html())
            self._ensure_viewer_server()
            self._open_app_window(f"http://127.0.0.1:{self.viewer_server.server_port}{route}")
        except Exception as exc:
            messagebox.showerror("Could not open video + equations", str(exc))

    def _open_jsxgraph(self):
        try:
            self._jsxgraph_html()
            self._ensure_viewer_server()
            self._open_app_window(f"http://127.0.0.1:{self.viewer_server.server_port}/jsxgraph")
        except Exception as exc:
            messagebox.showerror("Could not open JSXGraph preview", str(exc))

    def _open_desmos(self):
        try:
            self._viewer_html()
            self._ensure_viewer_server()
            self._open_app_window(f"http://127.0.0.1:{self.viewer_server.server_port}/")
        except Exception as exc:
            messagebox.showerror("Could not open Desmos", str(exc))

    def _ensure_viewer_server(self):
        if self.viewer_server is not None:
            return
        app = self
        class Handler(BaseHTTPRequestHandler):
            def _reply(inner, status, payload):
                body = json.dumps(payload).encode("utf-8")
                inner.send_response(status)
                inner.send_header("Content-Type", "application/json; charset=utf-8")
                inner.send_header("Content-Length", str(len(body)))
                inner.end_headers()
                inner.wfile.write(body)
            def do_GET(inner):
                try:
                    if inner.path == "/desmos-skin.png":
                        if not DESMOS_SKIN_PATH.is_file():
                            inner.send_error(404, "The Desmos UI skin is missing.")
                            return
                        body = DESMOS_SKIN_PATH.read_bytes()
                        inner.send_response(200)
                        inner.send_header("Content-Type", "image/png")
                        inner.send_header("Cache-Control", "public, max-age=3600")
                        inner.send_header("Content-Length", str(len(body)))
                        inner.end_headers()
                        inner.wfile.write(body)
                        return
                    if inner.path.startswith("/video-equations/frame"):
                        from urllib.parse import parse_qs, urlsplit
                        try:
                            index = int(parse_qs(urlsplit(inner.path).query).get("index", ["-1"])[0])
                        except ValueError:
                            index = -1
                        frames = app.video_equation_frames
                        if not isinstance(frames, list) or index < 0 or index >= len(frames):
                            inner._reply(404, {"error": "Equation frame was not found."})
                            return
                        source = frames[index]
                        if "expressions" in source:
                            expressions = source.get("expressions", [])
                        elif app.video_equation_cache_dir is not None:
                            cached_path = app.video_equation_cache_dir / f"{index:08d}.json.gz"
                            if not cached_path.is_file():
                                inner._reply(404, {"error": "The matching frame equations were not found."})
                                return
                            with gzip.open(cached_path, "rt", encoding="utf-8") as cache_file:
                                cached = json.load(cache_file)
                            expressions = _stream_frame_expressions(cached, app.stream_source_width, app.stream_source_height)
                        else:
                            expressions = []
                        inner._reply(200, {"index": index, "time": source.get("time", 0), "expressions": expressions})
                        return
                    if inner.path.startswith("/rendered-video"):
                        with app.video_lock:
                            output = app.video_output
                            ready = output is not None and output.is_file() and app.video_writer is None
                        if not ready:
                            inner.send_error(404, "Rendered video is not ready.")
                            return
                        size = output.stat().st_size
                        requested = inner.headers.get("Range", "")
                        start, end = 0, size - 1
                        if requested.startswith("bytes="):
                            bounds = requested[6:].split(",", 1)[0].split("-", 1)
                            start = int(bounds[0] or 0)
                            end = min(int(bounds[1]) if bounds[1] else end, end)
                            if start > end or start >= size:
                                inner.send_error(416, "Invalid byte range.")
                                return
                        length = end - start + 1
                        inner.send_response(206 if requested else 200)
                        inner.send_header("Content-Type", "video/mp4")
                        inner.send_header("Accept-Ranges", "bytes")
                        inner.send_header("Content-Length", str(length))
                        if requested:
                            inner.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                        inner.end_headers()
                        with output.open("rb") as stream:
                            stream.seek(start)
                            remaining = length
                            while remaining:
                                chunk = stream.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                inner.wfile.write(chunk)
                                remaining -= len(chunk)
                        return
                    if inner.path.startswith("/animation/frame"):
                        from urllib.parse import parse_qs, urlsplit
                        try:
                            index = int(parse_qs(urlsplit(inner.path).query).get("index", ["-1"])[0])
                        except ValueError:
                            index = -1
                        if index < 0 or index >= len(app.animation):
                            inner._reply(404, {"error": "Animation frame was not found."})
                            return
                        body = json.dumps(app.animation[index], separators=(",", ":")).encode("utf-8")
                        inner.send_response(200)
                        inner.send_header("Content-Type", "application/json; charset=utf-8")
                        inner.send_header("Content-Length", str(len(body)))
                        inner.end_headers()
                        inner.wfile.write(body)
                        return
                    if inner.path.startswith("/stream/points"):
                        from urllib.parse import parse_qs, urlsplit
                        index = int(parse_qs(urlsplit(inner.path).query).get("index", ["-1"])[0])
                        points_dir = app.stream_points_dir
                        if points_dir is None or index < 0 or index >= app.stream_frame_total:
                            inner._reply(404, {"error": "Rendered frame data was not found."}); return
                        cached = points_dir / f"{index:08d}.json.gz"
                        if not cached.is_file():
                            inner._reply(404, {"error": "Rendered frame data is not ready."}); return
                        with gzip.open(cached, "rb") as stream: body = stream.read()
                        inner.send_response(200); inner.send_header("Content-Type", "application/json; charset=utf-8")
                        inner.send_header("Content-Length", str(len(body))); inner.end_headers(); inner.wfile.write(body); return
                    if inner.path == "/stream/next":
                        with app.video_lock:
                            cap = app.stream_capture
                            if cap is None:
                                inner._reply(409, {"error": "Start the full-FPS render first."})
                                return
                            ok, frame = cap.read()
                            if not ok:
                                inner._reply(200, {"done": True})
                                return
                            app.stream_frame_index += 1
                            index, total, fps = app.stream_frame_index - 1, app.stream_frame_total, app.stream_source_fps
                            source_width, source_height = app.stream_source_width, app.stream_source_height
                        app.events.put(("stream_frame_read", (index, total)))
                        height, width = frame.shape[:2]
                        scale = min(1.0, 960 / max(width, height))
                        if scale < 1:
                            frame = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
                        height, width = frame.shape[:2]
                        edges = cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 50, 60)
                        pixels = np.argwhere(edges == 255)
                        scale_x = source_width / width if source_width > 0 else 1.0
                        scale_y = source_height / height if source_height > 0 else 1.0
                        coords = [(round(x*scale_x), round((height-y)*scale_y)) for y, x in pixels]
                        point_chunks = [{"x": [p[0] for p in coords[i:i+9999]], "y": [p[1] for p in coords[i:i+9999]]} for i in range(0, len(coords), 9999)]
                        points_dir = app.stream_points_dir
                        if points_dir is not None:
                            with gzip.open(points_dir / f"{index:08d}.json.gz", "wt", encoding="utf-8") as cache:
                                json.dump({"points": point_chunks}, cache, separators=(",", ":"))
                        body = json.dumps({"done": False, "index": index, "total": total, "fps": fps, "width": source_width or width, "height": source_height or height, "points": point_chunks}, separators=(",", ":")).encode("utf-8")
                        inner.send_response(200)
                        inner.send_header("Content-Type", "application/json; charset=utf-8")
                        inner.send_header("Content-Length", str(len(body)))
                        inner.end_headers()
                        inner.wfile.write(body)
                        return
                    if inner.path == "/streamer":
                        page = app._streamer_html()
                    elif inner.path == "/jsxgraph":
                        page = app._jsxgraph_html()
                    elif inner.path == "/video-equations":
                        page = app._video_equations_html()
                    elif inner.path == "/jsxgraph-video":
                        page = app._jsxgraph_video_html()
                    else:
                        page = app._viewer_html()
                    body = page.encode("utf-8")
                    inner.send_response(200)
                    inner.send_header("Content-Type", "text/html; charset=utf-8")
                    inner.send_header("Cache-Control", "no-store")
                    inner.send_header("Content-Length", str(len(body)))
                    inner.end_headers()
                    inner.wfile.write(body)
                except Exception as exc:
                    inner._reply(500, {"error": str(exc)})
            def do_POST(inner):
                try:
                    length = int(inner.headers.get("Content-Length", "0"))
                    data = json.loads(inner.rfile.read(length) or b"{}")
                    if inner.path == "/stream/start":
                        if not app.video_path or not app.video_path.exists():
                            inner._reply(400, {"error": "Choose a readable source video first."})
                            return
                        cap = cv2.VideoCapture(str(app.video_path))
                        if not cap.isOpened():
                            inner._reply(400, {"error": "OpenCV could not open the source video."})
                            return
                        fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
                        source_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                        try:
                            requested_seconds = float(data.get("seconds", 0))
                        except (TypeError, ValueError):
                            cap.release()
                            inner._reply(400, {"error": "Clip seconds must be a number between 1 and 20."})
                            return
                        if not 1 <= requested_seconds <= 20:
                            cap.release()
                            inner._reply(400, {"error": "Clip seconds must be between 1 and 20."})
                            return
                        total = min(source_total, max(1, round(requested_seconds * (fps if fps > 0 else 24.0)))) if source_total > 0 else 0
                        if total <= 0:
                            cap.release()
                            inner._reply(400, {"error": "Could not read the source video's frame count."})
                            return
                        source_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                        source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                        with app.video_lock:
                            if app.stream_capture is not None:
                                app.stream_capture.release()
                            if app.video_writer is not None:
                                app.video_writer.release()
                            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                            MP4_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                            app.job_dir = OUTPUT_DIR / uuid.uuid4().hex[:12]
                            app.job_dir.mkdir(parents=True, exist_ok=True)
                            app.stream_points_dir = OUTPUT_DIR / ".stream_cache" / app.job_dir.name
                            app.stream_points_dir.mkdir(parents=True, exist_ok=True)
                            app.stream_animation_json = app.job_dir / "desmos_sketch_full_fps_animation.json"
                            app.animation_mode = "sketch_full_fps"
                            app.stream_capture = cap
                            app.stream_source_fps = fps if fps > 0 else 24.0
                            app.stream_source_width = source_width
                            app.stream_source_height = source_height
                            app.stream_frame_total = total
                            app.stream_frame_index = 0
                            app.video_fps = app.stream_source_fps
                            timestamp = datetime.now().strftime("%H-%M-%S_%Y-%m-%d")
                            app.video_output = MP4_OUTPUT_DIR / f"{timestamp}.mp4"
                            app.video_animation_json = None
                            app.video_render_kind = "desmos"
                            app.video_equation_frames = None
                            app.video_equation_cache_dir = app.stream_points_dir
                            app.video_writer = None
                            app.video_size = None
                            app.video_frame_count = 0
                        app.events.put(("stream_started", app.job_dir))
                        inner._reply(200, {"total": total, "fps": app.stream_source_fps, "seconds": total / app.stream_source_fps})
                        return
                    if inner.path in ("/record/start",):
                        renderer = str(data.get("renderer", "desmos")).lower()
                        if renderer not in ("desmos", "jsxgraph"):
                            inner._reply(400, {"error": "Unknown animation renderer."})
                            return
                        with app.video_lock:
                            if app.video_writer is not None:
                                app.video_writer.release()
                            MP4_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                            timestamp = datetime.now().strftime("%H-%M-%S_%Y-%m-%d")
                            app.video_output = MP4_OUTPUT_DIR / f"{timestamp}.mp4"
                            app.video_animation_json = app.animation_output_path if app.animation_output_path and app.animation_output_path.is_file() else None
                            app.video_equation_frames = list(app.animation) if app.video_animation_json else None
                            app.video_render_kind = renderer
                            app.video_fps = max(1.0, min(float(data.get("fps", app.sample_fps)), 30.0))
                            app.video_frame_count = 0
                            app.video_size = None
                            app.video_writer = None
                        inner._reply(200, {"ok": True})
                        return
                    if inner.path in ("/record/frame", "/stream/frame"):
                        encoded = str(data.get("image", ""))
                        if "," in encoded:
                            encoded = encoded.split(",", 1)[1]
                        image_bytes = base64.b64decode(encoded)
                        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if frame is None:
                            raise ValueError("Desmos returned an unreadable screenshot.")
                        with app.video_lock:
                            if app.video_writer is None:
                                height, width = frame.shape[:2]
                                width -= width % 2
                                height -= height % 2
                                if width < 2 or height < 2:
                                    raise RuntimeError("The rendered frame is too small to encode as H.264.")
                                if (frame.shape[1], frame.shape[0]) != (width, height):
                                    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                                codec = cv2.VideoWriter_fourcc(*"avc1")
                                backend = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
                                writer = cv2.VideoWriter(str(app.video_output), backend, codec, app.video_fps, (width, height))
                                if not writer.isOpened():
                                    writer.release()
                                    raise RuntimeError("Could not create an H.264 MP4. The system H.264 encoder is unavailable.")
                                app.video_writer = writer
                                app.video_size = (width, height)
                            expected = app.video_size
                            if expected and (frame.shape[1], frame.shape[0]) != expected:
                                frame = cv2.resize(frame, expected, interpolation=cv2.INTER_AREA)
                            app.video_writer.write(frame)
                            app.video_frame_count += 1
                            count = app.video_frame_count
                            total = app.stream_frame_total if inner.path == "/stream/frame" else count
                        if inner.path == "/stream/frame":
                            app.events.put(("stream_frame_saved", (count, total)))
                        inner._reply(200, {"frames": count})
                        return
                    if inner.path in ("/record/finish", "/stream/finish"):
                        with app.video_lock:
                            if app.video_writer is not None:
                                app.video_writer.release()
                                app.video_writer = None
                            if inner.path == "/stream/finish" and app.stream_capture is not None:
                                app.stream_capture.release()
                                app.stream_capture = None
                            count = app.video_frame_count
                            output = app.video_output
                            points_dir = app.stream_points_dir if inner.path == "/stream/finish" else None
                            json_path = getattr(app, "stream_animation_json", None) if inner.path == "/stream/finish" else None
                            stream_fps = app.stream_source_fps
                        if not count or output is None or not output.is_file() or output.stat().st_size == 0:
                            inner._reply(400, {"error": "No playable MP4 was finalized. The output file is missing or empty."})
                        else:
                            payload = {"frames": count, "file": output.name, "path": str(output)}
                            app.events.put(("recorded", output))
                            if points_dir is not None and json_path is not None:
                                try:
                                    _save_stream_animation_json(points_dir, count, stream_fps, json_path,
                                                                app.stream_source_width, app.stream_source_height)
                                    loader_path = json_path.with_name("desmos_player_program.js")
                                    loader_path.write_text(_desmos_player_program(count, stream_fps), encoding="utf-8")
                                    app.video_animation_json = json_path
                                    app.video_equation_cache_dir = points_dir
                                    app.video_equation_frames = [{"time": i / max(float(stream_fps), 1.0)} for i in range(count)]
                                    app.video_fps = stream_fps
                                    app.events.put(("stream_animation_saved", (json_path, count, stream_fps, points_dir)))
                                    payload["animation_json"] = str(json_path)
                                    payload["player_loader"] = str(loader_path)
                                except Exception as exc:
                                    payload["animation_json_error"] = str(exc)
                            inner._reply(200, payload)
                        return
                    inner._reply(404, {"error": "Unknown recording action."})
                except Exception as exc:
                    inner._reply(500, {"error": str(exc)})
            def log_message(inner, *_args):
                pass
        self.viewer_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.viewer_server.serve_forever, daemon=True).start()

    def _export_player_loader(self):
        selected = filedialog.askopenfilename(
            title="Choose an Anime-to-Desmos animation JSON",
            initialdir=str(self.job_dir) if self.job_dir and self.job_dir.exists() else str(OUTPUT_DIR),
            filetypes=[("Animation JSON", "desmos_*_animation.json"), ("JSON files", "*.json")])
        if not selected:
            return
        animation_path = Path(selected)
        try:
            payload = json.loads(animation_path.read_text(encoding="utf-8"))
            frames = payload.get("frames")
            if not isinstance(frames, list) or not frames:
                raise ValueError("This JSON does not contain traced animation frames.")
            fps = float(payload.get("sample_fps", self.sample_fps))
            player_path = animation_path.with_name("desmos_player_program.js")
            player_path.write_text(_desmos_player_program(frames, fps), encoding="utf-8")
            self.animation_output_dir = animation_path.parent
            self.open_output_kind = "animation"
            self.open_btn.configure(text="Open animation folder", state="normal")
            self.status.configure(text=f"DesmosPlayer loader saved beside {animation_path.name}.")
            messagebox.showinfo(
                "DesmosPlayer loader created",
                f"Loader:\n{player_path}\n\nAnimation data:\n{animation_path}\n\nIn Desmos.com with the DesmosPlayer userscript enabled, add + → Program, paste the loader, click its round icon, select this animation JSON when prompted, then click Start.")
        except Exception as exc:
            messagebox.showerror("Could not create DesmosPlayer loader", str(exc))

    def _open_output(self):
        if self.open_output_kind == "animation" and self.animation_output_dir and self.animation_output_dir.exists():
            folder = self.animation_output_dir
        elif self.video_output and self.video_output.exists():
            folder = self.video_output.parent
        elif MP4_OUTPUT_DIR.exists():
            folder = MP4_OUTPUT_DIR
        elif OUTPUT_DIR.exists():
            folder = OUTPUT_DIR
        else:
            folder = self.job_dir
        if folder and folder.exists():
            os.startfile(str(folder))


def main():
    root = tk.Tk()
    AnimeDesmosDesktop(root)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

