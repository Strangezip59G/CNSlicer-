"""Convert an AniLines raster to a smooth, black-and-white SVG with VTracer."""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

try:
    import cairosvg
except (ImportError, OSError):
    cairosvg = None
import cv2
import numpy as np
import vtracer
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace an AniLines output image into SVG.")
    parser.add_argument("image", help="AniLines grayscale line-art PNG.")
    args = parser.parse_args()
    source = Path(args.image).expanduser().resolve()
    if not source.is_file():
        parser.error(f"Input image not found: {source}")
    gray = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        parser.error(f"Could not read input image: {source}")
    height, width = gray.shape
    stem = source.stem.removesuffix("_anilines_detail")
    svg_path = RESULTS / f"{stem}_anilines_vtracer.svg"
    preview_path = RESULTS / f"{stem}_anilines_vtracer_preview.png"
    sheet_path = RESULTS / f"{stem}_anilines_vtracer_comparison.png"

    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    rgba = np.dstack((rgb, np.full((height, width), 255, dtype=np.uint8)))
    config = vtracer.Config(
        clustering="bw", mode="spline", filter_speckle=1,
        binary_threshold=128, simplify=0.75, optimize=1, path_precision=3,
    )
    start = time.perf_counter()
    svg = config.convert_pixels(rgba.tobytes(), width, height)
    trace_seconds = time.perf_counter() - start
    svg_path.write_text(svg, encoding="utf-8")
    if cairosvg:
        try:
            cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(preview_path),
                             output_width=width, output_height=height, background_color="#ffffff")
        except Exception:
            pass

    path_count = len(re.findall(r"<path\b", svg))
    command_count = sum(len(re.findall(r"[MmLlHhVvCcSsQqTtAaZz]", d))
                        for d in re.findall(r'<path\b[^>]*\bd="([^"]*)"', svg))
    original = Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))
    if preview_path.exists():
        vector = Image.open(preview_path).convert("RGB")
        panel_w, panel_h = width, height
        canvas = Image.new("RGB", (2 * panel_w + 56, panel_h + 100), "#f2f4f7")
        draw = ImageDraw.Draw(canvas)
        try:
            title = ImageFont.truetype("segoeui.ttf", 19)
            note = ImageFont.truetype("segoeui.ttf", 15)
        except OSError:
            title = note = ImageFont.load_default()
        draw.text((20, 10), "AniLines raster → VTracer spline SVG", fill="#17212b", font=title)
        for index, (label, image) in enumerate((("AniLines input", original), ("SVG preview", vector))):
            x = 20 + index * (panel_w + 16)
            draw.text((x, 40), label, fill="#17212b", font=note)
            canvas.paste(image, (x, 66))
        draw.text((20, panel_h + 74),
                  f"{path_count:,} paths · {command_count:,} path commands · {svg_path.stat().st_size:,} bytes · {trace_seconds:.2f}s",
                  fill="#34495e", font=note)
        canvas.save(sheet_path)
    print(f"SVG:        {svg_path}")
    print(f"Preview:    {preview_path}")
    print(f"Comparison: {sheet_path}")
    print(f"Paths:      {path_count:,}; commands: {command_count:,}; tracing: {trace_seconds:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
