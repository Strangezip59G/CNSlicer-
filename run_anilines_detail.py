"""Run AniLines Detail on one image in this isolated test folder."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent
REPO = ROOT / "AniLines-source"
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
import torch  # noqa: E402
from infer import inference, load_model  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract anime line art with AniLines Detail.")
    parser.add_argument("image", nargs="?", default=str(REPO / "input" / "demo.png"))
    parser.add_argument("--output", default="", help="Output PNG path (optional).")
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        parser.error(f"Image not found: {image_path}")

    weights_path = REPO / "weights" / "detail.pth"
    if not weights_path.is_file():
        parser.error(f"AniLines Detail checkpoint is missing: {weights_path}")

    output_path = Path(args.output).expanduser().resolve() if args.output else ROOT / "results" / f"{image_path.stem}_anilines_detail.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    torch.set_num_threads(max(1, os.cpu_count() or 1))
    model_args = SimpleNamespace(mode="detail", device="cpu", fp16=False, binarize=-1.0)

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        parser.error(f"Could not read image: {image_path}")

    start = time.perf_counter()
    # The upstream inference script resolves checkpoint paths from its working directory.
    os.chdir(REPO)
    model = load_model(model_args)
    lineart = inference(image, model=model, args=model_args)
    elapsed = time.perf_counter() - start
    if not cv2.imwrite(str(output_path), lineart):
        raise OSError(f"Could not save result: {output_path}")

    print(f"Input:  {image_path}")
    print(f"Output: {output_path}")
    print(f"Size:   {image.shape[1]} x {image.shape[0]}")
    print(f"Time:   {elapsed:.2f} seconds (CPU, includes model load)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
