"""Render saved JSXGraph frame geometry to an MP4 using local CPU workers."""
from __future__ import annotations

import json
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import cv2
import numpy as np


def _color_bgr(value: str) -> tuple[int, int, int]:
    value = str(value or "#111111").strip().lower()
    named = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "blue": "#0000ff"}
    value = named.get(value, value)
    if len(value) == 4 and value.startswith("#"):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if len(value) == 7 and value.startswith("#"):
        try:
            red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))
            return blue, green, red
        except ValueError:
            pass
    return 17, 17, 17


def _nice_step(span: float) -> float:
    raw = max(float(span) / 10.0, 1.0)
    power = 10 ** int(np.floor(np.log10(raw)))
    fraction = raw / power
    return power * (1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10)


def _render_geometry(task: tuple[str, int, int, int]) -> np.ndarray:
    path_text, width, height, supersample = task
    cv2.setNumThreads(1)
    with Path(path_text).open("r", encoding="utf-8") as source:
        frame_data = json.load(source)

    high_w, high_h = width * supersample, height * supersample
    image = np.full((high_h, high_w, 3), 255, dtype=np.uint8)
    bounds = frame_data.get("graph_bounds") or {}
    left = float(bounds.get("left", 0))
    right = float(bounds.get("right", frame_data.get("image_width", width)))
    bottom = float(bounds.get("bottom", 0))
    top = float(bounds.get("top", frame_data.get("image_height", height)))
    span_x, span_y = max(right - left, 1e-9), max(top - bottom, 1e-9)
    sx, sy = high_w / span_x, high_h / span_y

    def pixel_points(points: list[list[float]]) -> np.ndarray:
        mapped = [[round((float(x) - left) * sx), round((top - float(y)) * sy)] for x, y in points]
        return np.asarray(mapped, dtype=np.int32).reshape((-1, 1, 2))

    # Draw a light graph grid first. Filled triangles naturally cover it, as they do in the graph.
    x_step, y_step = _nice_step(span_x), _nice_step(span_y)
    for x in np.arange(np.ceil(left / x_step) * x_step, right, x_step):
        px = round((x - left) * sx)
        cv2.line(image, (px, 0), (px, high_h - 1), (226, 226, 226), supersample, cv2.LINE_AA)
    for y in np.arange(np.ceil(bottom / y_step) * y_step, top, y_step):
        py = round((top - y) * sy)
        cv2.line(image, (0, py), (high_w - 1, py), (226, 226, 226), supersample, cv2.LINE_AA)
    if left <= 0 <= right:
        px = round((0 - left) * sx)
        cv2.line(image, (px, 0), (px, high_h - 1), (145, 145, 145), supersample, cv2.LINE_AA)
    if bottom <= 0 <= top:
        py = round((top - 0) * sy)
        cv2.line(image, (0, py), (high_w - 1, py), (145, 145, 145), supersample, cv2.LINE_AA)

    for expression in frame_data.get("curves", frame_data.get("expressions", [])):
        geometry = expression.get("geometry") or {}
        points = geometry.get("points") or []
        if len(points) < 2:
            continue
        pixels = pixel_points(points)
        color = _color_bgr(expression.get("color", "#111111"))
        kind = geometry.get("type")
        if kind == "polygon" and len(points) >= 3:
            opacity = float(expression.get("fillOpacity", 1.0) or 0.0)
            if opacity >= 0.999:
                cv2.fillPoly(image, [pixels], color, lineType=cv2.LINE_AA)
            elif opacity > 0:
                layer = image.copy()
                cv2.fillPoly(layer, [pixels], color, lineType=cv2.LINE_AA)
                cv2.addWeighted(layer, opacity, image, 1.0 - opacity, 0, dst=image)
        elif kind == "curve":
            opacity = float(expression.get("lineOpacity", 1.0) or 0.0)
            if opacity <= 0:
                continue
            line_width = max(1, round(float(expression.get("lineWidth", 2) or 2) * supersample))
            if opacity >= 0.999:
                cv2.polylines(image, [pixels], False, color, line_width, cv2.LINE_AA)
            else:
                layer = image.copy()
                cv2.polylines(layer, [pixels], False, color, line_width, cv2.LINE_AA)
                cv2.addWeighted(layer, opacity, image, 1.0 - opacity, 0, dst=image)

    if supersample > 1:
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    return image


def render_animation_to_mp4(
    frame_paths: list[Path],
    output_path: Path,
    fps: float,
    source_size: tuple[int, int],
    progress=None,
) -> dict:
    """Rasterize frames in separate CPU processes and encode them sequentially to MP4."""
    if not frame_paths:
        raise ValueError("There are no equation frames to render.")
    source_width, source_height = source_size
    if source_width < 2 or source_height < 2:
        raise ValueError("Could not determine the source video dimensions.")
    scale = min(1.0, 1920.0 / max(source_width, source_height))
    width = max(2, int(source_width * scale))
    height = max(2, int(source_height * scale))
    width -= width % 2
    height -= height % 2
    supersample = 2
    workers = max(1, min(4, (os.cpu_count() or 2) - 1, len(frame_paths)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(output_path.stem + ".partial.mp4")
    partial_path.unlink(missing_ok=True)

    writer = cv2.VideoWriter(
        str(partial_path), cv2.CAP_MSMF if os.name == "nt" else cv2.CAP_ANY,
        cv2.VideoWriter_fourcc(*"avc1"), float(fps), (width, height),
    )
    codec = "H.264"
    if not writer.isOpened():
        writer.release()
        writer = cv2.VideoWriter(
            str(partial_path), cv2.CAP_ANY, cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (width, height),
        )
        codec = "MPEG-4"
    if not writer.isOpened():
        writer.release()
        partial_path.unlink(missing_ok=True)
        raise RuntimeError("No MP4 encoder is available on this computer.")

    tasks = [(str(path), width, height, supersample) for path in frame_paths]
    waiting: dict = {}
    ready: dict[int, np.ndarray] = {}
    next_submit = 0
    next_write = 0
    total = len(tasks)
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            while next_submit < min(total, workers * 2):
                waiting[pool.submit(_render_geometry, tasks[next_submit])] = next_submit
                next_submit += 1
            while waiting:
                done, _ = wait(waiting, return_when=FIRST_COMPLETED)
                for future in done:
                    index = waiting.pop(future)
                    ready[index] = future.result()
                    if next_submit < total:
                        waiting[pool.submit(_render_geometry, tasks[next_submit])] = next_submit
                        next_submit += 1
                while next_write in ready:
                    writer.write(ready.pop(next_write))
                    next_write += 1
                    if progress is not None:
                        progress(next_write, total)
        writer.release()
        if next_write != total or not partial_path.is_file() or partial_path.stat().st_size == 0:
            raise RuntimeError("The CPU renderer did not finish every animation frame.")
        partial_path.replace(output_path)
    except Exception:
        writer.release()
        partial_path.unlink(missing_ok=True)
        raise
    return {"frames": total, "fps": float(fps), "width": width, "height": height,
            "workers": workers, "supersample": supersample, "codec": codec,
            "path": output_path}
