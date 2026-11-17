#!/usr/bin/env python3
"""Image loading, product fitting and composition without workflow state."""

from pathlib import Path

from PIL import Image


def load_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            return source.convert("RGBA")
    except OSError as exc:
        raise ValueError(f"ASSET_UNREADABLE: {path}: {exc}") from exc


def load_background(path: Path, expected: dict | None = None) -> Image.Image:
    background = load_image(path)
    width, height = background.size
    if width * 4 != height * 3:
        raise ValueError("BACKGROUND_NOT_3_4")
    if expected is not None and background.size != (expected["width"], expected["height"]):
        raise ValueError("BACKGROUND_RASTER_MISMATCH")
    return background


def load_product_layer(path: Path, size: tuple[int, int]) -> Image.Image:
    layer = load_image(path)
    if layer.size != size:
        raise ValueError("CACHED_LAYER_SIZE_MISMATCH")
    if layer.getchannel("A").getbbox() is None:
        raise ValueError("CACHED_PRODUCT_LAYER_EMPTY")
    return layer


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


def prepare_product_layer(path: Path, rect: dict, size: tuple[int, int]) -> Image.Image:
    product, left, top = fit_product_in_box(load_image(path), rect, *size)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.alpha_composite(product, (left, top))
    return layer


def compose(background: Image.Image, product_layer: Image.Image, overlay: Image.Image) -> Image.Image:
    """Combine prepared layers; callers own paths, caching and publishing."""
    image = Image.alpha_composite(background, product_layer).convert("RGB").convert("RGBA")
    image.alpha_composite(overlay.resize(background.size, Image.Resampling.LANCZOS))
    return image.convert("RGB")
