#!/usr/bin/env python3
"""Composite product, deterministic shadow, Logo, and text in one pass."""

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from common import verify_information_assets
from measure_text import configuration_overlay


ANGLE_DEGREES = 50
PRODUCT_HEIGHT_FRAC = 0.54
PRODUCT_MAX_WIDTH_FRAC = 0.52
WIDE_PRODUCT_ASPECT = 1.35
WIDE_PRODUCT_MAX_WIDTH_FRAC = 0.68
TALL_PRODUCT_ASPECT = 0.90
PRODUCT_RIGHT_MARGIN_FRAC = 0.12
PRODUCT_BOTTOM_MARGIN_FRAC = 0.18
TALL_PRODUCT_BOTTOM_MARGIN_FRAC = 0.12
SHADOW_DISTANCE_FRAC = 0.16
SHADOW_BLUR_FRAC = 0.007
SHADOW_OPACITY = 0.28
PRODUCT_ALPHA_CORE = 224
TITLE_COLOR = (44, 44, 44)
VERSION_COLOR = (90, 90, 90)
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


def clean_product(product: Image.Image) -> Image.Image:
    alpha = product.getchannel("A")
    if alpha.getextrema() == (255, 255):
        return product
    core = alpha.point(lambda value: 255 if value >= PRODUCT_ALPHA_CORE else 0)
    bbox = core.getbbox()
    if bbox is None:
        return product
    expansion = max(3, round(min(product.size) * 0.004))
    if expansion % 2 == 0:
        expansion += 1
    keep = core.filter(ImageFilter.MaxFilter(expansion))
    cleaned = product.copy()
    cleaned.putalpha(ImageChops.multiply(alpha, keep))
    bbox = cleaned.getchannel("A").getbbox()
    if bbox is None:
        return product
    return cleaned.crop(bbox)


def placement_limits(aspect: float) -> tuple[float, float]:
    if aspect >= WIDE_PRODUCT_ASPECT:
        return WIDE_PRODUCT_MAX_WIDTH_FRAC, PRODUCT_BOTTOM_MARGIN_FRAC
    if aspect < TALL_PRODUCT_ASPECT:
        return PRODUCT_MAX_WIDTH_FRAC, TALL_PRODUCT_BOTTOM_MARGIN_FRAC
    return PRODUCT_MAX_WIDTH_FRAC, PRODUCT_BOTTOM_MARGIN_FRAC


def fit_product(
    product: Image.Image, width: int, height: int, max_width_frac: float
) -> Image.Image:
    scale = min(
        height * PRODUCT_HEIGHT_FRAC / product.height,
        width * max_width_frac / product.width,
    )
    size = (max(1, round(product.width * scale)), max(1, round(product.height * scale)))
    return product.resize(size, Image.Resampling.LANCZOS)


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


def asset_path(reusable_dir: Path, rect: dict) -> Path:
    try:
        path = (reusable_dir / rect["asset"]).resolve()
    except (KeyError, TypeError) as exc:
        raise ValueError("LAYOUT_ASSET_INVALID") from exc
    if path.parent != reusable_dir:
        raise ValueError("LAYOUT_ASSET_OUTSIDE_REUSABLE")
    return path


def paste_layer(
    image: Image.Image,
    reusable_dir: Path,
    rect: dict,
    color: tuple[int, int, int] | None,
    width: int,
    height: int,
) -> None:
    left, top, target_width, target_height = target_box(rect, width, height)
    layer = load_image(asset_path(reusable_dir, rect), "L" if color else "RGBA").resize(
        (target_width, target_height), Image.Resampling.LANCZOS
    )
    if color:
        mask = layer
        layer = Image.new("RGBA", mask.size, color + (255,))
        layer.putalpha(mask)
    image.alpha_composite(layer, (left, top))


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--output-kind", required=True, choices=OUTPUT_KINDS)
    parser.add_argument("--product-layer", required=True)
    parser.add_argument("--shadow-mask", required=True)
    parser.add_argument("--reuse-layers", action="store_true")
    parser.add_argument("--configuration")
    args = parser.parse_args()

    background_path = Path(args.background).expanduser().resolve()
    product_path = Path(args.product).expanduser().resolve()
    layout_path = Path(args.layout).expanduser().resolve()
    reusable_dir = layout_path.parent
    product_dir = reusable_dir.parent
    output = Path(args.output).expanduser().resolve()
    product_layer_path = Path(args.product_layer).expanduser().resolve()
    shadow_mask_path = Path(args.shadow_mask).expanduser().resolve()

    try:
        if layout_path != product_dir / "reusable" / "layout.json":
            raise ValueError("LAYOUT_PATH_INVALID")
        validate_output(args.output_kind, output, product_dir)
        with layout_path.open(encoding="utf-8") as handle:
            layout = json.load(handle)
        if layout.get("product_directory") != str(product_dir):
            raise ValueError("PRODUCT_DIRECTORY_MISMATCH")
        verify_information_assets(layout, reusable_dir)

        background = load_image(background_path, "RGBA")
        width, height = background.size
        if width * 4 != height * 3 or layout.get("canvas", {}).get("ratio") != "3:4":
            raise ValueError("BACKGROUND_NOT_3_4")

        if args.reuse_layers:
            product_layer = load_image(product_layer_path, "RGBA")
            shadow = load_image(shadow_mask_path, "L")
            if product_layer.size != background.size or shadow.size != background.size:
                raise ValueError("CACHED_LAYER_SIZE_MISMATCH")
            if product_layer.getchannel("A").getbbox() is None:
                raise ValueError("CACHED_PRODUCT_LAYER_EMPTY")
        elif layout.get("style") == "meta-clear-a":
            product, left, top = fit_product_in_box(load_image(product_path, "RGBA"), layout["elements"]["PRODUCT_RECT"], width, height)
            product_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
            product_layer.alpha_composite(product, (left, top))
            # Existing artifact manifests retain their shadow entry; A has no cast shadow.
            shadow = Image.new("L", background.size, 0)
        else:
            product = clean_product(load_image(product_path, "RGBA"))
            max_width_frac, bottom_margin_frac = placement_limits(
                product.width / product.height
            )
            product = fit_product(product, *background.size, max_width_frac)
            right = width - round(width * PRODUCT_RIGHT_MARGIN_FRAC)
            bottom = height - round(height * bottom_margin_frac)
            left = right - product.width
            top = bottom - product.height
            if left < 0 or top < 0:
                raise ValueError("PRODUCT_PLACEMENT_OUTSIDE_CANVAS")
            product_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
            product_layer.alpha_composite(product, (left, top))
            product_mask = product_layer.getchannel("A")
            distance = product.height * SHADOW_DISTANCE_FRAC
            radians = math.radians(ANGLE_DEGREES)
            offset_x = round(distance * math.cos(radians))
            offset_y = round(distance * math.sin(radians))
            shadow = Image.new("L", background.size, 0)
            shadow.paste(product_mask, (offset_x, offset_y))
            blur = max(1, round(height * SHADOW_BLUR_FRAC))
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
            shadow = shadow.point(lambda value: round(value * SHADOW_OPACITY))

        shadow_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
        shadow_layer.putalpha(shadow)
        image = Image.alpha_composite(background, shadow_layer)
        image = Image.alpha_composite(image, product_layer).convert("RGB").convert("RGBA")

        elements = layout["elements"]
        if args.configuration is not None:
            overlay = configuration_overlay(layout, reusable_dir, args.configuration)
            image.alpha_composite(overlay.resize((width, height), Image.Resampling.LANCZOS))
        elif layout.get("style") == "meta-clear-a":
            paste_layer(image, reusable_dir, elements["INFORMATION_OVERLAY_RECT"], None, width, height)
        else:
            paste_layer(image, reusable_dir, elements["LOGO_RECT"], None, width, height)
            for rect in elements["TITLE_LINE_RECT"]:
                paste_layer(image, reusable_dir, rect, TITLE_COLOR, width, height)
            if "VERSION_TEXT_RECT" in elements:
                paste_layer(image, reusable_dir, elements["VERSION_TEXT_RECT"], VERSION_COLOR, width, height)

        new_output(output)
        image.convert("RGB").save(output, "PNG")
        if not args.reuse_layers:
            for path in (product_layer_path, shadow_mask_path):
                new_output(path)
            product_layer.save(product_layer_path, "PNG")
            shadow.save(shadow_mask_path, "PNG")
    except (KeyError, TypeError, OSError, json.JSONDecodeError, ValueError) as exc:
        sys.exit(str(exc))

    json.dump({"output": str(output)}, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
