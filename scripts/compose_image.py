#!/usr/bin/env python3
"""Composite the Meta product and cached information overlay in one pass."""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from common import load_layout
from measure_text import configuration_overlay


OUTPUT_KINDS = ("CANDIDATE", "SKU_PREVIEW")


def load_image(path: Path, mode: str) -> Image.Image:
    try:
        with Image.open(path) as source:
            image = source.convert(mode)
            image.load()
            return image
    except OSError as exc:
        raise ValueError(f"ASSET_UNREADABLE: {path}: {exc}") from exc


def new_output(path: Path) -> None:
    if path.exists():
        raise ValueError(f"REFUSE_OVERWRITE: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def target_box(rect: dict, width: int, height: int) -> tuple[int, int, int, int]:
    try:
        left = round(float(rect["x"]) * width)
        top = round(float(rect["y"]) * height)
        target_width = max(1, round(float(rect["w"]) * width))
        target_height = max(1, round(float(rect["h"]) * height))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("LAYOUT_RECT_INVALID") from exc
    if left < 0 or top < 0 or left + target_width > width or top + target_height > height:
        raise ValueError("LAYOUT_RECT_OUTSIDE_CANVAS")
    return left, top, target_width, target_height


def fit_product_in_box(product: Image.Image, rect: dict, width: int, height: int):
    bbox = product.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("PRODUCT_EMPTY")
    product = product.crop(bbox)
    x, y, box_width, box_height = target_box(rect, width, height)
    scale = min(box_width/product.width, box_height/product.height)
    product = product.resize((max(1, round(product.width*scale)), max(1, round(product.height*scale))), Image.Resampling.LANCZOS)
    return product, x+(box_width-product.width)//2, y+(box_height-product.height)//2


def validate_output(kind: str, output: Path, product_dir: Path) -> None:
    if output.parent != product_dir or output.suffix.lower() != ".png":
        raise ValueError("OUTPUT_PATH_INVALID")
    if kind == "CANDIDATE":
        valid = output.name.startswith("master-candidate-") and output.name.endswith("-preview.png")
    else:
        valid = output.name.startswith("SKU_VARIANT-") and output.name.endswith("-preview.png")
    if not valid:
        raise ValueError(f"{kind}_PATH_INVALID")
    if output.exists():
        raise ValueError(f"REFUSE_OVERWRITE: {output}")


def compose(
    background_path: Path,
    product_path: Path,
    layout_path: Path,
    output: Path,
    output_kind: str,
    product_layer_path: Path,
    configuration: str | None = None,
    *,
    reuse_layers: bool = False,
) -> Path:
    background_path = background_path.expanduser().resolve()
    product_path = product_path.expanduser().resolve()
    layout_path = layout_path.expanduser().resolve()
    output = output.expanduser().resolve()
    product_layer_path = product_layer_path.expanduser().resolve()
    try:
        layout, product_dir = load_layout(layout_path)
        reusable_dir = product_dir / "reusable"
        validate_output(output_kind, output, product_dir)

        background = load_image(background_path, "RGBA")
        width, height = background.size
        if width * 4 != height * 3 or layout.get("canvas", {}).get("ratio") != "3:4":
            raise ValueError("BACKGROUND_NOT_3_4")

        if reuse_layers:
            product_layer = load_image(product_layer_path, "RGBA")
            if product_layer.size != background.size:
                raise ValueError("CACHED_LAYER_SIZE_MISMATCH")
            if product_layer.getchannel("A").getbbox() is None:
                raise ValueError("CACHED_PRODUCT_LAYER_EMPTY")
        else:
            product, left, top = fit_product_in_box(load_image(product_path, "RGBA"), layout["elements"]["PRODUCT_RECT"], width, height)
            product_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
            product_layer.alpha_composite(product, (left, top))

        image = Image.alpha_composite(background, product_layer).convert("RGB").convert("RGBA")

        elements = layout["elements"]
        if configuration is not None:
            overlay = configuration_overlay(layout, reusable_dir, configuration)
            image.alpha_composite(overlay.resize((width, height), Image.Resampling.LANCZOS))
        else:
            rect = elements["INFORMATION_OVERLAY_RECT"]
            left, top, layer_width, layer_height = target_box(rect, width, height)
            overlay = load_image(reusable_dir / rect["asset"], "RGBA")
            image.alpha_composite(overlay.resize((layer_width, layer_height), Image.Resampling.LANCZOS), (left, top))

        new_output(output)
        image.convert("RGB").save(output, "PNG")
        if not reuse_layers:
            new_output(product_layer_path)
            product_layer.save(product_layer_path, "PNG")
    except (KeyError, TypeError) as exc:
        raise ValueError(str(exc)) from exc
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-kind", required=True, choices=OUTPUT_KINDS)
    parser.add_argument("--product-layer", type=Path, required=True)
    parser.add_argument("--reuse-layers", action="store_true")
    parser.add_argument("--configuration")
    args = parser.parse_args()

    try:
        output = compose(args.background, args.product, args.layout, args.output,
                         args.output_kind, args.product_layer, args.configuration,
                         reuse_layers=args.reuse_layers)
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
    json.dump({"output": str(output)}, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
