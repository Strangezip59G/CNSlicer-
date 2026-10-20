from __future__ import annotations
from pathlib import Path
import os
import re
import threading
import xml.etree.ElementTree as ET
import cv2
import numpy as np
from collections import Counter

try:
    import vtracer
except ImportError:  # Keep the existing OpenCV tracer available as a fallback.
    vtracer = None

_COLOR_KMEANS_LOCK = threading.Lock()

def process_video(video_path: Path, output_dir: Path, requested_seconds: float = 15, sample_fps: float = 2) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("OpenCV could not open this video. Try converting it to MP4 (H.264).")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    source_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if total <= 0:
        cap.release()
        raise ValueError("The video has no readable frames.")
    duration = total / fps
    clip_seconds = max(0.5, min(float(requested_seconds), duration, 20.0))
    sample_fps = max(1.0, min(float(sample_fps), 120.0))
    times = np.arange(0, clip_seconds, 1.0 / sample_fps)
    saved = []
    source_rate = sample_fps >= fps * 0.99
    for idx, seconds in enumerate(times):
        if not source_rate:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(seconds) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        # Keep native 1080p detail for sketch tracing. Higher-resolution sources
        # are still bounded to 1920 px on their longest side.
        scale = min(1.0, 1920 / max(w, h))
        if scale < 1:
            frame = cv2.resize(frame, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)
        name = f"frame_{idx:04d}.jpg"
        cv2.imwrite(str(output_dir / name), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        saved.append({"name": name, "time": round(float(seconds), 3),
                      "url": f"/outputs/{output_dir.name}/{name}"})
    cap.release()
    if not saved:
        raise ValueError("No frames could be extracted.")
    return {"duration_seconds": round(duration, 3), "processed_seconds": round(clip_seconds, 3),
            "source_width": source_width, "source_height": source_height,
            "source_fps": round(fps, 3), "sample_fps": sample_fps, "frames": saved,
            "notice": "This prototype traces one sampled frame at a time. It does not yet automatically track a character across the whole clip."}

def _rgb_to_hex(bgr):
    b, g, r = [int(x) for x in bgr]
    return f"#{r:02x}{g:02x}{b:02x}"

def _color_latex(hex_color):
    # Desmos API accepts CSS color strings as expression color metadata; this is a safe fallback.
    return hex_color

def _cubic_axis(p1, c1, c2, p2):
    a = -p1 + 3*c1 - 3*c2 + p2
    b = 3*p1 - 6*c1 + 3*c2
    c = -3*p1 + 3*c1
    return f"({a:.5f})t^3+({b:.5f})t^2+({c:.5f})t+({p1:.5f})"


def _cubic_samples(p1, c1, c2, p2, steps=24):
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        points.append([
            u*u*u*p1[0] + 3*u*u*t*c1[0] + 3*u*t*t*c2[0] + t*t*t*p2[0],
            u*u*u*p1[1] + 3*u*u*t*c1[1] + 3*u*t*t*c2[1] + t*t*t*p2[1],
        ])
    return points

def trace_frame_to_desmos(frame_path: Path, max_width: int = 640, colors: int = 8,
                          threshold: float = 0.0002, max_segments: int = 1200,
                          smoothing: int = 2, curve_smoothing: int = 6, fill_regions: bool = True, triangle_size: int = 18,
                          graph_width: int | None = None, graph_height: int | None = None) -> dict:
    img = cv2.imread(str(frame_path))
    if img is None:
        raise ValueError("Could not read the selected frame.")
    h, w = img.shape[:2]
    scale = min(1.0, max_width / max(w, h))
    nw, nh = max(1, int(w*scale)), max(1, int(h*scale))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    # Bilateral smoothing reduces compression grain while preserving anime line edges.
    smoothing = max(0, min(int(smoothing), 10))
    curve_smoothing = max(0, min(int(curve_smoothing), 10))
    if smoothing:
        sigma = 12.0 + smoothing * 5.0
        img = cv2.bilateralFilter(img, d=0, sigmaColor=sigma, sigmaSpace=1.0 + smoothing)
    # Cluster perceptual Lab colors so similar shades stay together more reliably.
    k = max(2, min(int(colors), 32))
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    data = lab.reshape((-1, 3)).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    # OpenCV's RNG seed is process-global. Protect seeding and k-means together
    # so parallel frame jobs remain deterministic instead of sharing RNG state.
    with _COLOR_KMEANS_LOCK:
        cv2.setRNGSeed(1)
        _, labels, palette = cv2.kmeans(data, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    label_map = labels.reshape(nh, nw).astype(np.uint8)
    # A tiny median pass removes isolated one-pixel color islands.
    label_map = cv2.medianBlur(label_map, 3)
    palette_lab = np.clip(palette, 0, 255).astype(np.uint8).reshape(1, k, 3)
    palette_bgr_arr = cv2.cvtColor(palette_lab, cv2.COLOR_LAB2BGR).reshape(k, 3)
    # Keep processing small for speed, but express coordinates in the source video's units.
    graph_w = max(1, int(graph_width or nw))
    graph_h = max(1, int(graph_height or nh))
    scale_x, scale_y = graph_w / nw, graph_h / nh

    expressions = []
    total_segments = 0
    # Background color is intentionally omitted; Desmos graph background is controlled by the host.
    # Convert the frame into a dense triangular mesh. Every grid cell is split into
    # two triangles, so the mesh covers the full image without gaps.
    palette_bgr = [tuple(map(int, c)) for c in palette_bgr_arr]
    triangle_size = max(8, min(int(triangle_size), 32))
    fill_expressions = []
    if fill_regions:
        tri_id = 0
        for y0 in range(0, nh, triangle_size):
            y1 = min(y0 + triangle_size, nh)
            for x0 in range(0, nw, triangle_size):
                x1 = min(x0 + triangle_size, nw)
                # Alternating diagonals avoid long repeated seams and create a mesh-like surface.
                if ((x0 // triangle_size) + (y0 // triangle_size)) % 2:
                    cell_triangles = [((x0,y0),(x1,y0),(x0,y1)), ((x1,y0),(x1,y1),(x0,y1))]
                else:
                    cell_triangles = [((x0,y0),(x1,y0),(x1,y1)), ((x0,y0),(x1,y1),(x0,y1))]
                for tri in cell_triangles:
                    # Bilinearly blend the four source pixels around the triangle center.
                    # This gives smooth local shades without allocating a mask per triangle.
                    fx = sum(pt[0] for pt in tri) / 3.0
                    fy = sum(pt[1] for pt in tri) / 3.0
                    fx = max(0.0, min(nw-1.001, fx))
                    fy = max(0.0, min(nh-1.001, fy))
                    ix, iy = int(fx), int(fy)
                    dx, dy = fx-ix, fy-iy
                    p00 = img[iy, ix].astype(np.float32)
                    p10 = img[iy, ix+1].astype(np.float32)
                    p01 = img[iy+1, ix].astype(np.float32)
                    p11 = img[iy+1, ix+1].astype(np.float32)
                    blended_bgr = ((1-dx)*(1-dy)*p00 + dx*(1-dy)*p10 +
                                   (1-dx)*dy*p01 + dx*dy*p11)
                    color_hex = _rgb_to_hex(blended_bgr)
                    coords = [(px*scale_x, (nh-py)*scale_y) for px,py in tri]
                    latex = r"\operatorname{polygon}\left(" + ",".join(
                        r"\left(" + f"{gx:.4f},{gy:.4f}" + r"\right)" for gx,gy in coords
                    ) + r"\right)"
                    fill_expressions.append({"id": f"tri_{tri_id}", "latex": latex,
                                             "color": color_hex, "fill": True, "fillOpacity": 1.0,
                                             "lineOpacity": 0, "lineWidth": 0,
                                             "geometry": {"type": "polygon", "points": [[gx, gy] for gx, gy in coords]},
                                             "secret": False, "label": ""})
                    tri_id += 1
    # Trace palette boundaries as smooth outlines over the triangular color mesh.
    items = []
    for pi, col in enumerate(palette_bgr):
        mask = (label_map == pi).astype(np.uint8) * 255
        # External contours avoid tracing nested holes and internal color seams twice.
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        color_hex = _rgb_to_hex(col)
        for contour in contours:
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            # Keep tiny ink regions; mouth, nostrils, eye lines, and hair marks can be small.
            if perimeter < 6 or area < max(1.0, nw*nh*threshold*0.02):
                continue
            items.append((max(area, perimeter*0.05), contour, perimeter, color_hex))
    # Add a dedicated high-contrast ink pass so thin black facial marks survive palette clustering.
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edge_map = cv2.Canny(gray, 45, 115, L2gradient=True)
    edge_contours, _ = cv2.findContours(edge_map, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    for contour in edge_contours:
        perimeter = cv2.arcLength(contour, False)
        if perimeter < 7:
            continue
        area = max(cv2.contourArea(contour), perimeter*0.05)
        items.append((area, contour, perimeter, "#17232c"))
    items.sort(key=lambda item: item[0], reverse=True)
    # Alternate large silhouettes and small details so neither scale consumes the full budget.
    ordered_items = []
    left, right = 0, len(items) - 1
    while left <= right:
        ordered_items.append(items[left])
        left += 1
        if left <= right:
            ordered_items.append(items[right])
            right -= 1
    for area, contour, perimeter, color_hex in ordered_items:
        if total_segments >= max_segments:
            break
        remaining = max_segments - total_segments
        contour_budget = min(remaining, 80)
        # Cap each contour so large shapes cannot starve fine details of line segments.
        epsilon = max(0.65, (0.0015 + min(float(threshold), 0.05) * 0.04) * perimeter)
        pts = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        while len(pts) > contour_budget and epsilon < max(2.0, perimeter * 0.08):
            epsilon *= 1.5
            pts = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        if len(pts) < 3:
            continue
        if len(pts) > remaining:
            continue
        # Each contour becomes a sequence of parametric line segments.
        # Coordinates are centered and y is inverted because image coordinates start at the top.
        coords = []
        for px, py in pts:
            gx = float(px)*scale_x
            gy = (nh - float(py))*scale_y
            coords.append((gx, gy))
        for i in range(len(coords)):
            if total_segments >= max_segments:
                break
            p0 = coords[(i-1) % len(coords)]
            p1 = coords[i]
            p2 = coords[(i+1) % len(coords)]
            p3 = coords[(i+2) % len(coords)]
            if curve_smoothing == 0:
                geometry_points = [[p1[0], p1[1]], [p2[0], p2[1]]]
                latex = (r"\left(" + f"{p1[0]:.4f}+({p2[0]-p1[0]:.4f})t," +
                         f"{p1[1]:.4f}+({p2[1]-p1[1]:.4f})t" + r"\right)\left\{0\le t\le1\right\}")
            else:
                tension = curve_smoothing / 10.0
                c1 = (p1[0] + (p2[0]-p0[0])*tension/6.0,
                      p1[1] + (p2[1]-p0[1])*tension/6.0)
                c2 = (p2[0] - (p3[0]-p1[0])*tension/6.0,
                      p2[1] - (p3[1]-p1[1])*tension/6.0)
                geometry_points = _cubic_samples(p1, c1, c2, p2)
                latex = (r"\left(" + _cubic_axis(p1[0], c1[0], c2[0], p2[0]) + "," +
                         _cubic_axis(p1[1], c1[1], c2[1], p2[1]) + r"\right)\left\{0\le t\le1\right\}")
            expressions.append({"id": f"seg_{total_segments}", "latex": latex,
                                "color": _color_latex(color_hex), "lineWidth": 2,
                                "geometry": {"type": "curve", "points": geometry_points},
                                "secret": False, "label": ""})
            total_segments += 1
    return {
        "expressions": fill_expressions + expressions,
        "count": len(fill_expressions) + len(expressions),
        "fill_count": len(fill_expressions),
        "triangle_size": triangle_size,
        "line_count": len(expressions),
        "image_width": nw,
        "image_height": nh,
        "graph_bounds": {"left": 0, "right": graph_w, "bottom": 0, "top": graph_h},
        "method": "Lab color quantization for contours, per-triangle source color blending, preserved small contours, high-contrast ink edges, and smooth cubic curves.",
        "settings": {"colors": k, "threshold": float(threshold), "max_segments": int(max_segments),
                     "smoothing": smoothing, "curve_smoothing": curve_smoothing, "fill_regions": bool(fill_regions), "triangle_size": triangle_size},
        "limitations": [
            "A dedicated high-contrast edge pass helps preserve small ink details such as mouth and nose marks, but source resolution and the line segment budget still limit detail.",
            "The triangular mesh covers the full frame; each triangle blends its source pixels into one shade, so smaller triangles preserve more local color variation.",
            "The current version maps one frame at a time and does not yet solve character identity or temporal tracking.",
            "A high segment count can slow Desmos. Start with 300–900 segments."
        ]
    }


def _automatic_canny(channel: np.ndarray) -> tuple[np.ndarray, float, float, float]:
    """Choose Canny thresholds from each frame channel's gradients and edge density."""
    softened = cv2.GaussianBlur(channel, (3, 3), 0.8)
    gx = cv2.Sobel(softened, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(softened, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy)
    gradients = magnitude[magnitude > 8.0]
    if gradients.size:
        high = float(np.percentile(gradients, 86) * 0.78)
    else:
        high = 30.0
    high = max(16.0, min(high, 140.0))
    low = max(7.0, high * 0.38)
    edge_map = np.zeros(channel.shape, dtype=np.uint8)
    density = 0.0
    # Busy or noisy channels get stricter thresholds; unusually flat channels
    # get more sensitive ones. This is calculated independently for each frame.
    for _ in range(5):
        edge_map = cv2.Canny(softened, low, high, L2gradient=True)
        density = float(cv2.countNonZero(edge_map)) / edge_map.size
        if density > 0.12 and _ < 4:
            high = min(180.0, high * 1.22)
            low = max(7.0, high * 0.38)
        elif density < 0.003 and high > 12.0 and _ < 4:
            high = max(12.0, high * 0.82)
            low = max(6.0, high * 0.38)
        else:
            break
    return edge_map, round(low, 2), round(high, 2), round(density, 5)


def _install_mask2former_transformers_compatibility() -> None:
    """Translate the AnimeSeg checkpoint's Transformers 4 keys for Transformers 5."""
    from anime_seg.mask2former.mask2former_model import Mask2FormerAnimeSegModel

    if getattr(Mask2FormerAnimeSegModel, "_anime_studio_compat", False):
        return

    def load_checkpoint_compatible(self, checkpoint_path: str):
        from safetensors.torch import load_file

        lower = str(checkpoint_path).lower()
        if lower.endswith(".safetensors"):
            state = load_file(str(checkpoint_path))
        elif lower.endswith((".pt", ".pth")):
            raw = torch.load(checkpoint_path, map_location="cpu")
            if not isinstance(raw, dict):
                raise RuntimeError("Unsupported checkpoint format in .pt/.pth file")
            state = next((raw[name] for name in ("state_dict", "model_state_dict", "model", "module")
                          if isinstance(raw.get(name), dict)), raw)
        else:
            raise RuntimeError(f"Unsupported checkpoint extension: {checkpoint_path}")

        state = {key.replace("_orig_mod.", ""): value for key, value in state.items()}
        # Transformers 5 wrapped the Swin backbone in `encoder.swin` and
        # renamed its attention/MLP projections. The AnimeSeg v3 checkpoint
        # uses the Transformers 4 names; convert those keys before matching.
        replacements = (
            (".pixel_level_module.encoder.embeddings.", ".pixel_level_module.encoder.swin.embeddings."),
            (".pixel_level_module.encoder.encoder.", ".pixel_level_module.encoder.swin.encoder."),
            (".pixel_level_module.encoder.layernorm.", ".pixel_level_module.encoder.swin.layernorm."),
            (".attention.self.query.", ".attention.q_proj."),
            (".attention.self.key.", ".attention.k_proj."),
            (".attention.self.value.", ".attention.v_proj."),
            (".attention.output.dense.", ".attention.o_proj."),
            (".attention.self.relative_position_bias_table",
             ".attention.relative_position_bias.relative_position_bias_table"),
            (".intermediate.dense.", ".mlp.fc1."),
            (".output.dense.", ".mlp.fc2."),
        )

        def remap_keys(source):
            result = {}
            for key, value in source.items():
                if key.endswith(".relative_position_index"):
                    # This is a derived index buffer, not a learned parameter.
                    continue
                for old, new in replacements:
                    key = key.replace(old, new)
                result[key] = value
            return result

        target = self.model.state_dict()
        variants = (state, remap_keys(state))
        candidates = []
        for variant in variants:
            candidates.extend((
                variant,
                {key[6:] if key.startswith("model.") else key: value
                 for key, value in variant.items()},
                {f"model.{key}": value for key, value in variant.items()},
            ))
        state = max(candidates, key=lambda candidate: len(set(target).intersection(candidate)))

        filtered = {}
        shape_mismatches = []
        for key, value in state.items():
            if key not in target:
                continue
            if target[key].shape != value.shape:
                shape_mismatches.append(key)
                continue
            filtered[key] = value
        if len(shape_mismatches) > 32:
            raise RuntimeError(
                f"Checkpoint has {len(shape_mismatches)} tensor shape mismatches after key conversion."
            )

        missing, unexpected = self.model.load_state_dict(filtered, strict=False)
        max_missing = max(32, int(0.2 * len(target)))
        if len(missing) > max_missing:
            raise RuntimeError(
                "Checkpoint compatibility mapping left "
                f"{len(missing)} of {len(target)} model parameters missing."
            )
        return missing, unexpected

    Mask2FormerAnimeSegModel.load_checkpoint = load_checkpoint_compatible
    Mask2FormerAnimeSegModel._anime_studio_compat = True


_ANIME_PART_COLORS = {
    0: (0, 0, 0),          # background
    1: (255, 220, 180),    # skin
    2: (100, 150, 255),    # face
    3: (255, 0, 0),        # hair
    4: (0, 255, 255),      # left eye
    5: (255, 255, 0),      # right eye
    6: (150, 255, 0),      # left eyebrow
    7: (0, 255, 100),      # right eyebrow
    8: (255, 140, 0),      # nose
    9: (255, 0, 150),      # mouth
    10: (180, 0, 255),     # clothes
    11: (128, 128, 0),     # accessory
}

# Each semantic part gets its own simplification and curve tension. Small facial
# features preserve more points; broad face and background strokes are smoother.
_SKETCH_REGION_STYLES = {
    "eye":       {"epsilon": 0.00070, "tension": 0.12, "stroke_cap": 110},
    "pupil":     {"epsilon": 0.00055, "tension": 0.08, "stroke_cap": 120},
    "eyelash":   {"epsilon": 0.00060, "tension": 0.10, "stroke_cap": 112},
    "blush":     {"epsilon": 0.00085, "tension": 0.15, "stroke_cap": 92},
    "lip":       {"epsilon": 0.00060, "tension": 0.10, "stroke_cap": 104},
    "brow":      {"epsilon": 0.00105, "tension": 0.22, "stroke_cap": 80},
    "nose":      {"epsilon": 0.00075, "tension": 0.14, "stroke_cap": 80},
    "mouth":     {"epsilon": 0.00075, "tension": 0.14, "stroke_cap": 88},
    "face":      {"epsilon": 0.00145, "tension": 0.62, "stroke_cap": 72},
    "skin":      {"epsilon": 0.00135, "tension": 0.48, "stroke_cap": 68},
    "hair":      {"epsilon": 0.00095, "tension": 0.32, "stroke_cap": 92},
    "hair_highlight":{"epsilon": 0.00078, "tension": 0.20, "stroke_cap": 104},
    "clothes":   {"epsilon": 0.00140, "tension": 0.38, "stroke_cap": 76},
    "dress":     {"epsilon": 0.00120, "tension": 0.34, "stroke_cap": 84},
    "accessory": {"epsilon": 0.00095, "tension": 0.18, "stroke_cap": 88},
    "charm":     {"epsilon": 0.00065, "tension": 0.12, "stroke_cap": 108},
    "foliage":   {"epsilon": 0.00110, "tension": 0.32, "stroke_cap": 76},
    "tree":      {"epsilon": 0.00135, "tension": 0.38, "stroke_cap": 68},
    "atmosphere":{"epsilon": 0.00190, "tension": 0.52, "stroke_cap": 56},
    "lighting":  {"epsilon": 0.00110, "tension": 0.28, "stroke_cap": 80},
    "profile":   {"epsilon": 0.00095, "tension": 0.20, "stroke_cap": 96},
    "background":{"epsilon": 0.00210, "tension": 0.54, "stroke_cap": 56},
    "other":     {"epsilon": 0.00150, "tension": 0.40, "stroke_cap": 60},
}

# Mean emitted curves from the earlier 48-frame Desmos-era output
# (outputs/198eb3094066). Use those counts as region-selection weights so the
# newer fine-face pass cannot crowd clothing and the rest of the character out.
_DESMOS_REGION_REFERENCE = {
    "atmosphere": 841, "dress": 678, "hair": 334, "skin": 144,
    "accessory": 141, "foliage": 110, "eye": 44, "mouth": 30,
    "brow": 29, "tree": 20, "face": 16, "pupil": 4, "nose": 5,
    "charm": 5, "profile": 2, "lip": 5, "eyelash": 2, "lighting": 3,
    "hair_highlight": 44,
}
_BODY_REGION_WEIGHTS = {
    "dress": _DESMOS_REGION_REFERENCE["dress"],
    "hair": _DESMOS_REGION_REFERENCE["hair"],
    "skin": _DESMOS_REGION_REFERENCE["skin"],
    "accessory": _DESMOS_REGION_REFERENCE["accessory"],
    "charm": 12,
    "hair_highlight": _DESMOS_REGION_REFERENCE["hair_highlight"],
}


def _weighted_region_selection(region_items: dict, limit: int,
                               weights: dict) -> list:
    """Select candidates in historical region proportions, redistributing empty quotas."""
    positions = Counter()
    selected = []
    region_order = {region: index for index, region in enumerate(region_items)}
    while len(selected) < limit:
        eligible = [region for region, items in region_items.items()
                    if positions[region] < len(items)]
        if not eligible:
            break
        region = max(eligible, key=lambda key: (
            float(weights.get(key, 1)) / (positions[key] + 1),
            -region_order[key],
        ))
        selected.append(region_items[region][positions[region]])
        positions[region] += 1
    return selected


def create_anime_part_segmenter(model_cache: Path | None = None):
    """Load the anime-specific parser once; its first run downloads the model."""
    if model_cache is not None:
        model_cache.mkdir(parents=True, exist_ok=True)
        import os
        os.environ.setdefault("HF_HOME", str(model_cache))
    try:
        import torch
        from anime_seg import AnimeSegPipeline
        _install_mask2former_transformers_compatibility()
    except ImportError as exc:
        raise RuntimeError(
            "Anime part parsing is not installed. Rebuild the desktop package with the current requirements."
        ) from exc
    # Avoid oversubscribing the CPU while the rest of the frame pipeline runs.
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 4)))
    return AnimeSegPipeline.from_mask2former(
        repo_id="suzukimain/AnimeSeg",
        filename="models/anime_seg_mask2former_v3.safetensors",
        device="cpu",
        base_model="facebook/mask2former-swin-base-ade-semantic",
    )


def _anime_part_labels(segmenter, bgr: np.ndarray, width: int, height: int,
                       inference_semaphore=None) -> np.ndarray:
    """Return semantic class IDs at the trace resolution from the parser's palette mask."""
    from PIL import Image
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    if inference_semaphore is None:
        mask = segmenter(image, width=width, height=height).convert("RGB")
    else:
        # The shared CPU model is read-only in eval mode. A RAM-aware semaphore
        # limits concurrent forwards while allowing frame workers to overlap.
        with inference_semaphore:
            mask = segmenter(image, width=width, height=height).convert("RGB")
    palette = np.asarray(mask, dtype=np.uint8)
    labels = np.full((height, width), 255, dtype=np.uint8)
    for class_id, color in _ANIME_PART_COLORS.items():
        labels[np.all(palette == color, axis=2)] = class_id
    # An unknown palette pixel is treated as background for conservative curves.
    labels[labels == 255] = 0
    return _refine_anime_part_labels(labels, bgr)


def _refine_anime_part_labels(labels: np.ndarray, bgr: np.ndarray) -> np.ndarray:
    """Split broad parser classes into conservative, detail-aware sketch regions.

    The parser has broad classes only. Color and shape cues below refine pixels
    inside those classes, leaving uncertain pixels in their original region.
    """
    # Keep existing IDs 0..11 and assign stable IDs to fine-grained overlays.
    # 12 pupil, 13 lashes, 14 blush, 15 lips, 16 hair highlights,
    # 17 foliage, 18 trees, 19 atmosphere, 20 lighting, 21 charms, 22 profiles.
    result = labels.copy()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hue, sat, value = cv2.split(hsv)
    dark = gray < 78
    pink = (((hue <= 12) | (hue >= 168)) & (sat > 48) & (value > 70))

    # Pupil cores sit inside the two parser eye masks; the outer dark eye pixels
    # become a distinct lash/eyeliner region to preserve their crisp detail.
    for eye_id in (4, 5):
        eye = (labels == eye_id).astype(np.uint8)
        if not cv2.countNonZero(eye):
            continue
        core = cv2.erode(eye, np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        pupil = core & dark
        if np.count_nonzero(pupil) >= 2:
            result[pupil] = 12
        lash = eye.astype(bool) & dark & ~pupil
        if np.count_nonzero(lash) >= 2:
            result[lash] = 13

    skin_face = np.isin(labels, (1, 2))
    blush = skin_face & pink
    result[blush] = 14
    mouth = labels == 9
    lip = mouth & pink
    result[lip] = 15

    hair = labels == 3
    # Relative bright hair strands are retained without relabeling broad white
    # regions in other classes. A local blur removes global lighting bias.
    local = cv2.GaussianBlur(gray, (0, 0), 9)
    hair_highlight = hair & (gray > 145) & (gray.astype(np.int16) - local.astype(np.int16) > 16)
    result[hair_highlight] = 16

    # Background vegetation and structural trunks/branches get their own curve
    # profiles. Geometry remains image-derived; these are cautious color cues.
    green = ((hue >= 24) & (hue <= 96) & (sat > 45) & (value > 32))
    background = labels == 0
    foliage = background & green
    result[foliage] = 17
    # Dark green low-saturation structures among green background pixels are a
    # tree/branch cue; leave bright saturated leaves classified as foliage.
    tree = background & green & (value < 105) & (sat < 150)
    result[tree] = 18

    # Pale, low-chroma background pixels get a soft-atmosphere profile. This can
    # preserve mist/smoke contours but does not claim semantic smoke detection.
    atmosphere = background & (sat < 48) & (value > 95)
    result[atmosphere] = 19

    # Preserve strong highlight boundaries as lighting detail across the frame,
    # except where a more specific facial/hair/foliage region already applies.
    protected = np.isin(result, (12, 13, 14, 15, 16, 17, 18, 21))
    lighting = (gray > 220) & ~protected & (labels != 0)
    result[lighting] = 20

    # Accessories can contain small jewelry/hair charms. Mark compact accessory
    # components separately; larger objects retain the broad accessory profile.
    accessory = (labels == 11).astype(np.uint8)
    count, components, stats, _ = cv2.connectedComponentsWithStats(accessory, 8)
    for component in range(1, count):
        area = int(stats[component, cv2.CC_STAT_AREA])
        width = int(stats[component, cv2.CC_STAT_WIDTH])
        height = int(stats[component, cv2.CC_STAT_HEIGHT])
        if 3 <= area <= 900 and max(width, height) <= 52:
            result[components == component] = 21

    # Mask2Former often leaves the mouth cavity inside the broad face/skin
    # classes, especially in wide shots. Recover dark mouth marks in the lower
    # half of each detected face box so Canny contours can receive a mouth style.
    face_mask = np.isin(labels, (1, 2)).astype(np.uint8)
    face_mask = cv2.morphologyEx(face_mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    face_count, face_components, face_stats, _ = cv2.connectedComponentsWithStats(face_mask, 8)
    h, w = gray.shape
    min_face_area = max(140, int(h * w * 0.00018))
    dark_or_red = (gray < 92) | (pink & (value < 150))
    for face_component in range(1, face_count):
        fx, fy, fw, fh, face_area = map(int, face_stats[face_component])
        if face_area < min_face_area or fw < 18 or fh < 16:
            continue
        x0 = fx + int(fw * 0.16); x1 = fx + int(fw * 0.86)
        y0 = fy + int(fh * 0.53); y1 = fy + int(fh * 0.96)
        if x1 <= x0 or y1 <= y0:
            continue
        roi = np.zeros((h, w), dtype=np.uint8)
        roi[y0:y1, x0:x1] = 255
        candidates = (dark_or_red & (roi > 0)).astype(np.uint8)
        mouth_count, mouth_components, mouth_stats, _ = cv2.connectedComponentsWithStats(candidates, 8)
        best_component = None
        best_score = 0.0
        for mouth_component in range(1, mouth_count):
            mx, my, mw, mh, area = map(int, mouth_stats[mouth_component])
            if area < 3 or mw > fw * 0.58 or mh > fh * 0.38:
                continue
            center_x = (mx + mw * 0.5 - fx) / max(1, fw)
            center_y = (my + mh * 0.5 - fy) / max(1, fh)
            if not (0.24 <= center_x <= 0.82 and 0.52 <= center_y <= 0.97):
                continue
            score = area / (1.0 + abs(center_x - 0.53) * 4.0)
            if score > best_score:
                best_score, best_component = score, mouth_component
        if best_component is not None:
            result[mouth_components == best_component] = 9

    # The external face/skin silhouette receives a dedicated side-profile curve
    # style. Only label boundaries touching hair/background are considered.
    face_mask = np.isin(labels, (1, 2)).astype(np.uint8)
    if cv2.countNonZero(face_mask):
        inner_edge = face_mask.astype(bool) & ~cv2.erode(face_mask, np.ones((3, 3), np.uint8)).astype(bool)
        nearby = cv2.dilate(face_mask, np.ones((5, 5), np.uint8))
        adjacent = (cv2.dilate((labels == 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) | \
                   (cv2.dilate((labels == 3).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
        profile = inner_edge & adjacent & (nearby > 0) & ~np.isin(result, (12, 13, 14, 15))
        result[profile] = 22

    return result


def _contour_region(contour: np.ndarray, labels: np.ndarray) -> str:
    """Choose the semantic class nearest most contour pixels, favoring facial details."""
    points = contour.reshape(-1, 2)
    if not len(points):
        return "other"
    h, w = labels.shape
    x = np.clip(points[:, 0].astype(np.int32), 0, w - 1)
    y = np.clip(points[:, 1].astype(np.int32), 0, h - 1)
    # Labels are coarse compared with the source edges. Sample a small local
    # neighborhood so a contour on a part boundary still receives that part's style.
    # The parser's feature masks (especially pupils, lashes and lips) can be
    # only a few pixels wide. A large vote window lets adjacent skin/background
    # labels outvote those details, so keep the neighborhood tight at HD sizes.
    # Use a 3x3 neighborhood at HD sizes. A 5x5 neighborhood lets broad skin,
    # hair, and background masks overwhelm narrow eye and mouth labels.
    radius = max(1, min(2, round(max(w, h) / 1920)))
    votes = np.zeros(23, dtype=np.int64)
    for dy in range(-radius, radius + 1, max(1, radius // 2)):
        yy = np.clip(y + dy, 0, h - 1)
        for dx in range(-radius, radius + 1, max(1, radius // 2)):
            xx = np.clip(x + dx, 0, w - 1)
            votes += np.bincount(labels[yy, xx].ravel(), minlength=23)[:23]
    # Resolve ties in favor of small features: their masks are narrow and are
    # otherwise easily outvoted by adjacent skin/hair pixels.
    priority = (12, 13, 15, 14, 8, 9, 4, 5, 6, 7, 16, 21, 20, 22, 18, 17, 3, 2, 1, 11, 10, 19, 0)
    class_id = max(priority, key=lambda item: (votes[item], -priority.index(item)))
    return {
        0: "background", 1: "skin", 2: "face", 3: "hair",
        4: "eye", 5: "eye", 6: "brow", 7: "brow", 8: "nose", 9: "mouth",
        10: "dress", 11: "accessory", 12: "pupil", 13: "eyelash",
        14: "blush", 15: "lip", 16: "hair_highlight", 17: "foliage",
        18: "tree", 19: "atmosphere", 20: "lighting", 21: "charm", 22: "profile",
    }.get(class_id, "other")


def _vtracer_curves(edges: np.ndarray, labels: np.ndarray | None, grid_cols: int,
                    grid_rows: int, filter_speckle: int = 1,
                    simplify: float = 0.75, min_length: float = 0.45) -> tuple[list, int]:
    """Trace an edge bitmap with VTracer and return semantic, tile-aware cubic segments."""
    if vtracer is None:
        return [], 0
    height, width = edges.shape
    gray = np.where(edges > 0, 0, 255).astype(np.uint8)
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    rgba = np.dstack((rgb, np.full((height, width), 255, dtype=np.uint8)))
    config = vtracer.Config(clustering="bw", mode="spline", filter_speckle=filter_speckle,
                            binary_threshold=128, simplify=simplify, optimize=1,
                            path_precision=3)
    svg = config.convert_pixels(rgba.tobytes(), width, height)
    root = ET.fromstring(svg)
    token_re = re.compile(r"([MmCcZz])|([-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?)")
    candidates = []
    col_w, row_h = width / grid_cols, height / grid_rows
    for path in root.findall(".//{*}path"):
        tokens = [cmd or float(number) for cmd, number in token_re.findall(path.get("d", ""))]
        i = 0
        command = None
        current = start = None
        segments = []
        while i < len(tokens):
            if isinstance(tokens[i], str):
                command = tokens[i]
                i += 1
                if command in "Zz":
                    if current is not None and start is not None and current != start:
                        delta = ((start[0]-current[0])/3, (start[1]-current[1])/3)
                        segments.append((current, (current[0]+delta[0], current[1]+delta[1]),
                                         (current[0]+2*delta[0], current[1]+2*delta[1]), start))
                    current = start
                    command = None
                    continue
            if command in "Mm" and i + 1 < len(tokens):
                x, y = float(tokens[i]), float(tokens[i+1]); i += 2
                current = (x, y) if command == "M" else (current[0]+x, current[1]+y)
                start = current
                command = "L" if command == "M" else "l"
                continue
            if command in "Cc" and i + 5 < len(tokens):
                vals = [float(v) for v in tokens[i:i+6]]; i += 6
                if command == "c":
                    c1 = (current[0]+vals[0], current[1]+vals[1])
                    c2 = (current[0]+vals[2], current[1]+vals[3])
                    end = (current[0]+vals[4], current[1]+vals[5])
                else:
                    c1, c2, end = (vals[0], vals[1]), (vals[2], vals[3]), (vals[4], vals[5])
                segments.append((current, c1, c2, end)); current = end
                continue
            # VTracer's spline output uses M/m, C/c and Z. If an unknown token
            # appears, discard this path and let the caller use OpenCV instead.
            segments = []
            break
        for p0, c1, c2, p3 in segments:
            length = sum(np.hypot((b[0]-a[0]), (b[1]-a[1])) for a, b in ((p0,c1),(c1,c2),(c2,p3)))
            if length < min_length:
                continue
            samples = []
            for t in (0.2, 0.4, 0.6, 0.8):
                u = 1-t
                samples.append([round(u**3*p0[0]+3*u*u*t*c1[0]+3*u*t*t*c2[0]+t**3*p3[0]),
                                round(u**3*p0[1]+3*u*u*t*c1[1]+3*u*t*t*c2[1]+t**3*p3[1])])
            region = _contour_region(np.asarray(samples, dtype=np.int32).reshape(-1,1,2), labels) if labels is not None else "unclassified"
            mx, my = (p0[0]+3*c1[0]+3*c2[0]+p3[0])/8, (p0[1]+3*c1[1]+3*c2[1]+p3[1])/8
            cell = (min(grid_rows-1, max(0, int(my/row_h))), min(grid_cols-1, max(0, int(mx/col_w))))
            candidates.append((region, cell, p0, c1, c2, p3, length))
    return candidates, len(root.findall(".//{*}path"))


def _emit_vtracer_expressions(candidates: list, nw: int, nh: int, target_w: int,
                              target_h: int, budget: int, grid_cols: int,
                              grid_rows: int) -> tuple[list, Counter, Counter, int, int, int, int]:
    """Apply face-priority to VTracer splines and use tiles only for fair ordering."""
    facial_priority = {"pupil":0,"eyelash":1,"eye":2,"brow":3,"nose":4,
                       "mouth":5,"lip":6,"profile":7,"face":8,"blush":9}
    grouped = {}
    for item in candidates:
        grouped.setdefault((item[0], item[1]), []).append(item)
    for values in grouped.values():
        # Alternate long contours with the shortest contours in the same
        # region and tile. Long outlines stay crisp, while tiny marks such as
        # lashes, pupils, fingers and cloth creases get an early place in the
        # limited per-frame expression budget.
        ranked = sorted(values, key=lambda item: item[6], reverse=True)
        balanced = []
        left, right = 0, len(ranked) - 1
        while left <= right:
            balanced.append(ranked[left])
            left += 1
            if left <= right:
                balanced.append(ranked[right])
                right -= 1
        values[:] = balanced
    ordered = []
    for region in sorted(facial_priority, key=facial_priority.get):
        cells = sorted(cell for r, cell in grouped if r == region)
        depth = 0
        while any((region, cell) in grouped and depth < len(grouped[(region,cell)]) for cell in cells):
            for cell in cells:
                if (region,cell) in grouped and depth < len(grouped[(region,cell)]):
                    ordered.append(grouped[(region,cell)][depth])
            depth += 1
    region_priority = (
        "face", "skin", "hair", "hair_highlight", "dress", "accessory",
        "charm", "foliage", "tree", "lighting", "atmosphere", "background",
        "other", "unclassified",
    )
    remaining_regions = {r for r, _ in grouped if r not in facial_priority}
    ordered_regions = [region for region in region_priority if region in remaining_regions]
    ordered_regions.extend(sorted(remaining_regions.difference(ordered_regions)))
    for region in ordered_regions:
        cells = sorted(cell for r, cell in grouped if r == region)
        depth = 0
        while any((region, cell) in grouped and depth < len(grouped[(region,cell)]) for cell in cells):
            for cell in cells:
                if (region,cell) in grouped and depth < len(grouped[(region,cell)]):
                    ordered.append(grouped[(region,cell)][depth])
            depth += 1
    facial_present = any(item[0] in facial_priority for item in candidates)
    face_budget = min(budget, int(budget*.40)) if facial_present else 0
    blush_limit = min(120, max(24, int(budget * 0.05)))
    used, region_used = Counter(), Counter()
    facial_selected = []
    blush_selected = 0
    for item in ordered:
        region = item[0]
        if region not in facial_priority or len(facial_selected) >= face_budget:
            continue
        if region == "blush" and blush_selected >= blush_limit:
            continue
        facial_selected.append(item)
        blush_selected += int(region == "blush")

    scene_by_region = {}
    for item in ordered:
        if item[0] not in facial_priority:
            scene_by_region.setdefault(item[0], []).append(item)
    body_by_region = {region: scene_by_region[region]
                      for region in _BODY_REGION_WEIGHTS if region in scene_by_region}
    body_budget = min(max(0, budget - len(facial_selected)), int(round(budget * 0.52)))
    body_selected = _weighted_region_selection(body_by_region, body_budget,
                                               _BODY_REGION_WEIGHTS)
    body_ids = {id(item) for item in body_selected}
    remaining_by_region = {
        region: [item for item in items if id(item) not in body_ids]
        for region, items in scene_by_region.items()
    }
    scene_selected = _weighted_region_selection(
        remaining_by_region, max(0, budget - len(facial_selected) - len(body_selected)),
        _DESMOS_REGION_REFERENCE,
    )
    selection_order = facial_selected + body_selected + scene_selected
    face_used = len(facial_selected)
    expressions = []
    sx, sy = target_w/nw, target_h/nh
    for region, cell, p0, c1, c2, p3, _ in selection_order:
        if len(expressions) >= budget:
            break
        def xy(point): return (point[0]*sx, (nh-point[1])*sy)
        q0, q1, q2, q3 = map(xy, (p0,c1,c2,p3))
        latex = (r"\left(" + _cubic_axis(q0[0],q1[0],q2[0],q3[0]) + "," +
                 _cubic_axis(q0[1],q1[1],q2[1],q3[1]) + r"\right)\left\{0\le t\le1\right\}")
        expressions.append({"id":f"ink_{len(expressions)}","latex":latex,
                            "color":"#111111","lineWidth":1.0,
                            "geometry":{"type":"curve","points":_cubic_samples(q0,q1,q2,q3)},
                            "secret":False,"label":"","region":region})
        used[cell] += 1; region_used[region] += 1
    body_used = sum(region_used[region] for region in _BODY_REGION_WEIGHTS)
    return expressions, region_used, used, face_budget, face_used, blush_limit, body_budget, body_used


def trace_frame_lineart_to_desmos(frame_path: Path, max_width: int = 1920,
                                  max_segments: int = 2400, smoothing: int = 2,
                                  curve_smoothing: int = 4,
                                  graph_width: int | None = None, graph_height: int | None = None,
                                  segmenter=None, segmenter_semaphore=None) -> dict:
    """Make a compact monochrome contour drawing for one animation frame."""
    img = cv2.imread(str(frame_path))
    if img is None:
        raise ValueError("Could not read the selected frame.")
    h, w = img.shape[:2]
    scale = min(1.0, max_width / max(w, h))
    nw, nh = max(2, int(w*scale)), max(2, int(h*scale))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    # Use a spatial grid for candidate balancing. The grid size scales with
    # the image dimensions so a small clip does not get fragmented into tiny
    # cells, while large frames still receive even curve coverage.
    grid_cols = max(4, min(12, int(round(nw / 240))))
    grid_rows = max(3, min(8, int(round(nh / 216))))
    cell_w, cell_h = nw / grid_cols, nh / grid_rows
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    smoothing = max(0, min(int(smoothing), 10))
    curve_smoothing = max(0, min(int(curve_smoothing), 10))
    channels = [gray, lab[:, :, 1], lab[:, :, 2]]
    if smoothing:
        channels = [cv2.bilateralFilter(ch, d=0, sigmaColor=12+smoothing*4,
                                        sigmaSpace=1+smoothing) for ch in channels]
    detected = [_automatic_canny(channel) for channel in channels]
    edges = cv2.bitwise_or(cv2.bitwise_or(detected[0][0], detected[1][0]), detected[2][0])
    part_labels = (_anime_part_labels(segmenter, img, nw, nh, segmenter_semaphore)
                   if segmenter is not None else None)
    feature_edges_added = 0
    fine_feature_edges = np.zeros_like(edges)
    if part_labels is not None:
        # Add a lower-threshold edge pass around parsed facial parts to recover
        # faint pupils, mouth lines, and lashes without adding global noise.
        facial_parts = np.isin(part_labels, (1, 2, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 22)).astype(np.uint8)
        face_zone = cv2.dilate(facial_parts, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        detail_edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 18, 46, L2gradient=True)
        edges = cv2.bitwise_or(edges, cv2.bitwise_and(detail_edges, face_zone * 255))
        # Anime segmentation often marks the iris, lash, brow, nose, or mouth
        # as a tiny semantic island even when its luminance edge is faint.
        # Preserve those island boundaries directly, and add chroma edges in
        # the local feature zone so colored eyes and lips keep their inner lines.
        feature_ids = (4, 5, 6, 7, 8, 9, 12, 13, 14, 15)
        feature_boundary_edges = np.zeros_like(edges)
        feature_boundary_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        for feature_id in feature_ids:
            feature_mask = (part_labels == feature_id).astype(np.uint8)
            if cv2.countNonZero(feature_mask) < 2:
                continue
            feature_boundary = cv2.morphologyEx(feature_mask, cv2.MORPH_GRADIENT,
                                                 feature_boundary_kernel)
            feature_boundary_edges = cv2.bitwise_or(feature_boundary_edges, feature_boundary * 255)
        small_feature_parts = np.isin(part_labels, feature_ids).astype(np.uint8)
        feature_zone = cv2.dilate(small_feature_parts,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        feature_chroma_edges = np.zeros_like(edges)
        for channel in (lab[:, :, 1], lab[:, :, 2]):
            chroma = cv2.Canny(cv2.GaussianBlur(channel, (3, 3), 0), 12, 32,
                               L2gradient=True)
            feature_chroma_edges = cv2.bitwise_or(feature_chroma_edges,
                                                   cv2.bitwise_and(chroma, feature_zone * 255))
        feature_edges_added = cv2.countNonZero(cv2.bitwise_or(feature_boundary_edges,
                                                               feature_chroma_edges))
        fine_feature_edges = cv2.bitwise_or(feature_boundary_edges, feature_chroma_edges)
        fine_feature_edges = cv2.bitwise_or(
            fine_feature_edges, cv2.bitwise_and(detail_edges, feature_zone * 255))
        # Preserve fine interior lines on hair, sleeves, robes and accessories
        # too. Keep this pass constrained to parsed character parts so it does
        # not bring back the background texture that the cleaner trace removed.
        character_parts = np.isin(part_labels, (1, 2, 3, 10, 11, 16, 21, 22)).astype(np.uint8)
        character_zone = cv2.dilate(character_parts, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        character_edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 26, 60, L2gradient=True)
        edges = cv2.bitwise_or(edges, cv2.bitwise_and(character_edges, character_zone * 255))
    edge_density = float(cv2.countNonZero(edges)) / edges.size
    target_w = max(1, int(graph_width or nw))
    target_h = max(1, int(graph_height or nh))
    requested_budget = max(100, min(int(max_segments), 5000))
    budget = requested_budget
    adaptive_extra = 0
    vtracer_fallback = None
    if vtracer is not None:
        try:
            vt_candidates, vt_path_count = _vtracer_curves(edges, part_labels, grid_cols, grid_rows)
            fine_candidate_count = 0
            if cv2.countNonZero(fine_feature_edges):
                fine_candidates, _ = _vtracer_curves(
                    fine_feature_edges, part_labels, grid_cols, grid_rows,
                    filter_speckle=0, simplify=0.15, min_length=0.12)
                # Fine paths are traced in a separate pass so global path
                # simplification cannot erase them. Drop near-identical paths
                # already emitted by the broad trace to avoid doubled ink.
                seen = set()
                for item in vt_candidates:
                    ends = sorted((tuple(round(value / 2) for value in item[2]),
                                   tuple(round(value / 2) for value in item[5])))
                    seen.add((item[0], item[1], ends[0], ends[1], round(item[6] / 2)))
                for item in fine_candidates:
                    ends = sorted((tuple(round(value / 2) for value in item[2]),
                                   tuple(round(value / 2) for value in item[5])))
                    key = (item[0], item[1], ends[0], ends[1], round(item[6] / 2))
                    if key not in seen:
                        vt_candidates.append(item)
                        seen.add(key)
                        fine_candidate_count += 1
                # Fine feature curves used to take spaces away from hair and
                # skin inside the fixed per-frame cap. Grow the frame budget
                # in proportion to the independently traced detail candidates
                # so the new detail adds strokes instead of replacing them.
                adaptive_extra = min(5000 - budget,
                                     int(np.ceil(fine_candidate_count * 0.20)))
                budget += adaptive_extra
            if vt_candidates:
                expressions, region_counts, selected_cells, face_budget, face_used, blush_limit, body_budget, body_used = _emit_vtracer_expressions(
                    vt_candidates, nw, nh, target_w, target_h, budget, grid_cols, grid_rows)
                if expressions:
                    return {"expressions":expressions,"count":len(expressions),"line_count":len(expressions),
                            "image_width":nw,"image_height":nh,
                            "graph_bounds":{"left":0,"right":target_w,"bottom":0,"top":target_h},
                            "method":"VTracer cubic spline edge paths assigned to anime parts; spatial tiles only balance selection order.",
                            "settings":{"max_segments":budget,"requested_max_segments":requested_budget,
                                        "adaptive_detail_extra_curves":adaptive_extra,
                                        "blush_curve_limit":blush_limit,
                                        "smoothing":smoothing,
                                        "curve_smoothing":curve_smoothing,"sensitivity":"automatic",
                                        "region_curves":bool(part_labels is not None),
                                        "region_detail_points":dict(region_counts),
                                        "facial_detail_budget":face_budget,"facial_detail_curves":face_used,
                                        "body_curve_budget":body_budget,"body_curve_count":body_used,
                                        "body_reference_distribution":_BODY_REGION_WEIGHTS,
                                        "body_reference_source":"48-frame earlier Desmos-era output; body regions weighted by observed curve counts",
                                        "spatial_cell_curve_limit":None,
                                        "facial_region_cell_limit":None,
                                        "spatial_selection":"round-robin ordering only; no per-cell curve rejection",
                                        "curve_selection":"face detail reserve plus 52% body quota weighted by earlier Desmos body-region counts; 1 px lines",
                                        "vtracer_speckle_filter":1,"vtracer_simplify":0.75,
                                        "semantic_feature_edge_pixels":feature_edges_added,
                                        "independent_fine_feature_curves":fine_candidate_count,
                                        "semantic_feature_edge_labels":["eye","brow","nose","mouth","pupil","eyelash","blush","lip"],
                                        "spatial_grid":{"columns":grid_cols,"rows":grid_rows},
                                        "spatial_cell_curves":{f"{row},{col}":count for (row,col),count in sorted(selected_cells.items())},
                                        "vtracer_paths":vt_path_count,"vtracer_candidate_curves":len(vt_candidates),
                                        "vtracer":"1.0.0a4","combined_edge_density":round(edge_density,5)}}
        except Exception as exc:
            # An unsupported SVG variation or an optional wheel problem should
            # not stop users from tracing: use the established OpenCV route.
            vtracer_fallback = f"{type(exc).__name__}: {exc}"
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        if perimeter < 4:
            continue
        region = _contour_region(contour, part_labels) if part_labels is not None else "unclassified"
        style = _SKETCH_REGION_STYLES.get(region, _SKETCH_REGION_STYLES["other"])
        # findContours can return open traces where an edge runs out of frame.
        # Treating those as closed joins distant endpoints with an invented
        # spline, which is a common source of long overlapping strokes.
        closed = cv2.norm(contour[0, 0].astype(np.float32) - contour[-1, 0].astype(np.float32)) <= 1.5
        epsilon = max(0.55, perimeter * (style["epsilon"] if part_labels is not None else 0.0025))
        points = cv2.approxPolyDP(contour, epsilon, closed).reshape(-1, 2)
        if len(points) >= 3:
            center_x, center_y = np.mean(points, axis=0)
            cell = (min(grid_rows - 1, max(0, int(center_y / cell_h))),
                    min(grid_cols - 1, max(0, int(center_x / cell_w))))
            candidates.append((perimeter, points, region, closed, cell))
    candidates.sort(key=lambda item: item[0], reverse=True)

    def alternate_long_and_short(items):
        ordered_items = []
        left, right = 0, len(items)-1
        while left <= right:
            ordered_items.append(items[left]); left += 1
            if left <= right:
                ordered_items.append(items[right]); right -= 1
        return ordered_items

    def spatial_round_robin(items):
        """Interleave contours by image cell so a busy area cannot use the whole budget."""
        cells = {}
        for item in items:
            cells.setdefault(item[4], []).append(item)
        for cell_items in cells.values():
            cell_items.sort(key=lambda item: item[0], reverse=True)
        ordered_items = []
        cell_order = sorted(cells)
        depth = 0
        while True:
            added = False
            for cell in cell_order:
                if depth < len(cells[cell]):
                    ordered_items.append(cells[cell][depth])
                    added = True
            if not added:
                break
            depth += 1
        return ordered_items

    # The old global stroke order spent the 2,400-expression budget on long
    # clothing/hair/background contours before reaching eyes and mouths. Reserve
    # part of the same budget for those facial details, then spend the remainder
    # on the rest of the scene. If a frame has no detected face details, the
    # reserved amount remains available to the other regions.
    facial_priority = {
        "pupil": 0, "eyelash": 1, "eye": 2, "brow": 3,
        "nose": 4, "mouth": 5, "lip": 6, "profile": 7,
        "face": 8, "blush": 9,
    }
    facial_candidates = []
    scene_candidates = candidates
    facial_order = []
    if part_labels is not None:
        facial_candidates = [c for c in candidates if c[2] in facial_priority]
        scene_candidates = [c for c in candidates if c[2] not in facial_priority]
        for region_name in sorted(facial_priority, key=facial_priority.get):
            region_items = [c for c in facial_candidates if c[2] == region_name]
            facial_order.extend(spatial_round_robin(region_items))
    scene_by_region = {}
    for contour_candidate in scene_candidates:
        scene_by_region.setdefault(contour_candidate[2], []).append(contour_candidate)
    body_by_region = {
        region: spatial_round_robin(scene_by_region[region])
        for region in _BODY_REGION_WEIGHTS if region in scene_by_region
    }
    body_order = _weighted_region_selection(
        body_by_region, sum(map(len, body_by_region.values())), _BODY_REGION_WEIGHTS)
    other_by_region = {
        region: spatial_round_robin(items)
        for region, items in scene_by_region.items() if region not in _BODY_REGION_WEIGHTS
    }
    other_order = _weighted_region_selection(
        other_by_region, sum(map(len, other_by_region.values())), _DESMOS_REGION_REFERENCE)
    ordered = facial_order + body_order + other_order

    scale_x, scale_y = target_w/nw, target_h/nh
    expressions = []
    # Sketch expressions are one smooth cubic each. Keep this cap explicit:
    # it bounds both the Desmos expression list and the per-frame JSON size.
    tension = curve_smoothing/10.0
    region_counts = Counter()
    facial_detail_budget = min(len(expressions) + int(budget * 0.30), budget) if facial_candidates else 0
    facial_detail_used = 0
    body_curve_budget = min(budget, int(round(budget * 0.52))) if body_by_region else 0
    body_curve_used = 0
    selected_cells = Counter()
    for _, points, region, closed, cell in ordered:
        if len(expressions) >= budget:
            break
        is_facial_detail = region in facial_priority and part_labels is not None
        is_body_region = region in _BODY_REGION_WEIGHTS and part_labels is not None
        if is_facial_detail and facial_detail_used >= facial_detail_budget:
            continue
        if is_body_region and body_curve_used >= body_curve_budget:
            continue
        style = _SKETCH_REGION_STYLES.get(region, _SKETCH_REGION_STYLES["other"])
        # Simplify further when a contour would consume too much of the remaining budget.
        available = budget-len(expressions)
        per_stroke_cap = min(available, style["stroke_cap"] if part_labels is not None else 60)
        if is_facial_detail:
            per_stroke_cap = min(per_stroke_cap, facial_detail_budget-facial_detail_used)
        if is_body_region:
            per_stroke_cap = min(per_stroke_cap, body_curve_budget-body_curve_used)
        detail_scale = style["epsilon"] / 0.0015 if part_labels is not None else 1.0
        epsilon = max(0.5, len(points)/per_stroke_cap * detail_scale)
        pts = cv2.approxPolyDP(points.reshape(-1,1,2), epsilon, closed).reshape(-1,2)
        while len(pts) > per_stroke_cap:
            epsilon *= 1.5
            pts = cv2.approxPolyDP(points.reshape(-1,1,2), epsilon, closed).reshape(-1,2)
        min_points = 3 if closed else 2
        if len(pts) < min_points:
            continue
        coords = [(float(x)*scale_x, (nh-float(y))*scale_y) for x,y in pts]
        tension = style["tension"] if part_labels is not None else curve_smoothing/10.0
        point_count = len(coords) if closed else len(coords) - 1
        for i in range(point_count):
            p1 = coords[i]
            p2 = coords[(i+1) % len(coords)]
            if is_facial_detail and facial_detail_used >= facial_detail_budget:
                break
            if is_body_region and body_curve_used >= body_curve_budget:
                break
            # Budget each emitted segment by its own image location. This is
            # what prevents one dense tile or one oversized contour from
            # monopolizing the frame-wide expression budget.
            next_index = (i+1) % len(pts)
            segment_x = (float(pts[i][0]) + float(pts[next_index][0])) * 0.5
            segment_y = (float(pts[i][1]) + float(pts[next_index][1])) * 0.5
            segment_cell = (min(grid_rows - 1, max(0, int(segment_y / cell_h))),
                            min(grid_cols - 1, max(0, int(segment_x / cell_w))))
            if closed:
                p0, p3 = coords[(i-1) % len(coords)], coords[(i+2) % len(coords)]
            else:
                p0 = coords[i-1] if i > 0 else (2*p1[0]-p2[0], 2*p1[1]-p2[1])
                p3 = coords[i+2] if i+2 < len(coords) else (2*p2[0]-p1[0], 2*p2[1]-p1[1])
            if (curve_smoothing == 0 and part_labels is None) or tension == 0:
                geometry_points = [[p1[0], p1[1]], [p2[0], p2[1]]]
                latex = (r"\left(" + f"{p1[0]:.3f}+({p2[0]-p1[0]:.3f})t," +
                         f"{p1[1]:.3f}+({p2[1]-p1[1]:.3f})t" +
                         r"\right)\left\{0\le t\le1\right\}")
            else:
                c1 = (p1[0]+(p2[0]-p0[0])*tension/6, p1[1]+(p2[1]-p0[1])*tension/6)
                c2 = (p2[0]-(p3[0]-p1[0])*tension/6, p2[1]-(p3[1]-p1[1])*tension/6)
                geometry_points = _cubic_samples(p1, c1, c2, p2)
                latex = (r"\left(" + _cubic_axis(p1[0],c1[0],c2[0],p2[0]) + "," +
                         _cubic_axis(p1[1],c1[1],c2[1],p2[1]) +
                         r"\right)\left\{0\le t\le1\right\}")
            expression = {"id": f"ink_{len(expressions)}", "latex": latex,
                          "color": "#111111", "lineWidth": 1.0,
                          "geometry": {"type": "curve", "points": geometry_points},
                          "secret": False, "label": ""}
            if part_labels is not None:
                expression["region"] = region
            expressions.append(expression)
            region_counts[region] += 1
            if is_facial_detail:
                facial_detail_used += 1
            if is_body_region:
                body_curve_used += 1
            selected_cells[segment_cell] += 1
    return {"expressions": expressions, "count": len(expressions),
            "line_count": len(expressions), "image_width": nw, "image_height": nh,
            "graph_bounds": {"left": 0, "right": target_w, "bottom": 0, "top": target_h},
            "method": ("Anime-part segmentation with facial detail reserve, Desmos-data-weighted body regions, round-robin spatial ordering, open-contour-safe spline fitting, and adaptive luminance/Lab-chroma edges."
                        if part_labels is not None else
                        "Per-frame adaptive luminance and Lab-chroma edge detection with compact monochrome contour curves."),
            "settings": {"max_segments": budget, "smoothing": smoothing,
                         "curve_smoothing": curve_smoothing, "sensitivity": "automatic",
                         "region_curves": bool(part_labels is not None),
                         "region_detail_points": dict(region_counts),
                         "facial_detail_budget": facial_detail_budget,
                         "facial_detail_curves": facial_detail_used,
                         "body_curve_budget": body_curve_budget,
                         "body_curve_count": body_curve_used,
                         "body_reference_distribution": _BODY_REGION_WEIGHTS,
                         "body_reference_source": "48-frame earlier Desmos-era output; body regions weighted by observed curve counts",
                         "spatial_grid": {"columns": grid_cols, "rows": grid_rows},
                         "spatial_cell_curve_limit": None,
                         "facial_region_cell_limit": None,
                         "spatial_selection": "round-robin ordering only; no per-cell curve rejection",
                         "line_width_pixels": 1,
                         "spatial_cell_curves": {f"{row},{col}": count
                                                  for (row, col), count in sorted(selected_cells.items())},
                         "canny_thresholds": [{"low": low, "high": high} for _, low, high, _ in detected],
                         "channel_edge_density": [density for _, _, _, density in detected],
                         "combined_edge_density": round(edge_density, 5),
                         "vtracer_fallback": vtracer_fallback}}



