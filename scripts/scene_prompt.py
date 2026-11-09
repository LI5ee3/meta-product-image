#!/usr/bin/env python3
"""Build deterministic Image Gen prompts without persistent attempt state."""

import argparse
import sys
from pathlib import Path

from common import atomic_write, read_json, verify_information_assets, verify_master


SKILL_DIR = Path(__file__).resolve().parent.parent
IMAGE_PROMPT_REFERENCE = SKILL_DIR / "references" / "image-gen-prompt.md"
PROMPT_ADDITIONS_NAME = "prompt-additions.json"
FINAL_SAFE_ZONE_MARGIN = 0.05
CURRENT_SKU_PALETTE_NAME = "current-sku-palette.json"
PRODUCT_AREA_POLICY = """Product and shadow placement policy:
FINAL_INFORMATION_SAFE_ZONE is the only area that must be empty.
Do not create or interpret any product or shadow area as another empty or unobstructed safe zone, including any previous temporary correction that requested one.
Simple, low-detail MD3 cards and their restrained elevation shadows may appear behind the future local product and may receive its local cast shadow.
Avoid only dominant high-contrast edges or dense detail that visibly competes with the product after local compositing."""
SKU_EDIT_BLOCK = """Use ORIGINAL_MASTER_BACKGROUND as the authoritative composition reference and SKU_PALETTE_REFERENCE only as the authoritative color reference. Keep the background composition unchanged and adapt only its colors to SKU_PALETTE_REFERENCE. Do not infer or copy any product identity, geometry, silhouette, screen, strap, label, icon, branding, text, or packaging from the palette reference. Generate the empty background plate only. Do not add any product, product shadow, Logo, or text."""


def image_prompt(layout: dict) -> str:
    reference = IMAGE_PROMPT_REFERENCE if layout.get("style") == "meta-clear-a" else SKILL_DIR / "references" / "image-gen-prompt-legacy-md3.md"
    try:
        prompt = reference.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise ValueError(
            f"REFERENCE_UNREADABLE: {IMAGE_PROMPT_REFERENCE}: {exc}"
        ) from exc
    if not prompt:
        raise ValueError("IMAGE_PROMPT_EMPTY")
    return prompt


def information_safe_zone_block(layout: dict) -> str:
    zones = layout.get("clear_zones")
    if layout.get("style") == "meta-clear-a":
        if not isinstance(zones, list) or {z.get("label") for z in zones} != {"TOP_INFORMATION", "BOTTOM_FEATURES"}:
            raise ValueError("INFORMATION_SAFE_ZONES_MISSING")
        lines = ["Two separate quiet information regions; do not merge them across the product:"]
        for zone in zones:
            x, y, w, h = (float(zone[k]) for k in ("x", "y", "w", "h"))
            if not (0 <= x < x+w <= 1 and 0 <= y < y+h <= 1):
                raise ValueError("INFORMATION_SAFE_ZONE_OUT_OF_CANVAS")
            lines.append(f"- {zone['label']}: x {x*100:.1f}%-{(x+w)*100:.1f}%, y {y*100:.1f}%-{(y+h)*100:.1f}%")
        lines.append("Keep both regions pale and calm. Leave the middle available for subtle layered curves behind the locally composited product. Do not generate the information panel, text or icons.")
        return "\n".join(lines)
    selected = [
        zone
        for zone in zones or []
        if zone.get("label") == "LOGO_RECT"
        or str(zone.get("label", "")).startswith("TITLE_LINE_RECT_")
        or zone.get("label") == "VERSION_TEXT_RECT"
    ]
    if not selected:
        raise ValueError("INFORMATION_SAFE_ZONES_MISSING")

    bounds = []
    for zone in selected:
        try:
            x0 = float(zone["x"])
            y0 = float(zone["y"])
            x1 = x0 + float(zone["w"])
            y1 = y0 + float(zone["h"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("INFORMATION_SAFE_ZONE_INVALID") from exc
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("INFORMATION_SAFE_ZONE_OUT_OF_CANVAS")
        bounds.append((x0, y0, x1, y1))

    x0 = max(0, min(bound[0] for bound in bounds) - FINAL_SAFE_ZONE_MARGIN)
    y0 = max(0, min(bound[1] for bound in bounds) - FINAL_SAFE_ZONE_MARGIN)
    x1 = min(1, max(bound[2] for bound in bounds) + FINAL_SAFE_ZONE_MARGIN)
    y1 = min(1, max(bound[3] for bound in bounds) + FINAL_SAFE_ZONE_MARGIN)
    return "\n".join(
        (
            "Layout-only final information safe zone:",
            'Interpret any earlier "top-left negative space" instruction only as this one merged rectangle.',
            "This rectangle merges the measured Logo, title-line, and optional version safe zones, then expands the union by 5.0% of the canvas on every side, clipped to the canvas:",
            f"- FINAL_INFORMATION_SAFE_ZONE: x {x0 * 100:.1f}%-{x1 * 100:.1f}%, y {y0 * 100:.1f}%-{y1 * 100:.1f}%",
            "Keep the entire rectangle clear, calm, smooth, and empty, free of card edges, strong shadows, texture changes, or detailed geometry.",
        )
    )


def load_layout(path: Path) -> tuple[dict, Path]:
    layout_path = path.expanduser().resolve()
    if layout_path.name != "layout.json":
        raise ValueError("LAYOUT_PATH_INVALID")
    layout = read_json(layout_path)
    reusable_dir = layout_path.parent
    if reusable_dir.name != "reusable":
        raise ValueError("LAYOUT_PATH_INVALID: expected <product>/reusable/layout.json")
    product_dir = reusable_dir.parent
    if layout.get("product_directory") != str(product_dir):
        raise ValueError("PRODUCT_DIRECTORY_MISMATCH")
    verify_information_assets(layout, reusable_dir)
    zones = layout.get("clear_zones")
    if not isinstance(zones, list) or not zones:
        raise ValueError("CLEAR_ZONES_MISSING")
    return layout, product_dir




def load_palette_payload(product_dir: Path, mode: str) -> dict:
    reusable = product_dir / "reusable"
    if mode == "MASTER":
        palette_path = reusable / "palette.json"
    else:
        pointer_path = reusable / CURRENT_SKU_PALETTE_NAME
        if not pointer_path.exists():
            raise ValueError("CURRENT_SKU_PALETTE_POINTER_MISSING")
        pointer = read_json(pointer_path)
        try:
            palette_path = Path(pointer["palette_json"]).expanduser().resolve()
        except (KeyError, TypeError) as exc:
            raise ValueError("CURRENT_SKU_PALETTE_POINTER_INVALID") from exc
    if not palette_path.exists():
        raise ValueError("PALETTE_JSON_MISSING")
    payload = read_json(palette_path)
    guidance = payload.get("background_guidance")
    if not isinstance(guidance, dict):
        raise ValueError("BACKGROUND_GUIDANCE_MISSING")
    return payload


def background_separation_block(palette_payload: dict, mode: str) -> str:
    guidance = palette_payload["background_guidance"]
    dominant = guidance["dominant_material_color"]
    constraints = guidance["constraints"]
    preferred = guidance.get("preferred_background_colors") or []
    forbidden = guidance.get("forbidden_background_colors") or []

    preferred_text = ", ".join(entry["hex"] for entry in preferred[:3])
    forbidden_text = ", ".join(entry["hex"] for entry in forbidden[:4])
    return "\n".join((
        f"Background separation policy for {mode}:",
        f"- Dominant material color: {dominant['hex']} (L* {dominant['lightness_lstar']:.1f}, hue {dominant['hue_degrees']:.1f}°).",
        f"- Keep the background visually separated from the product with a minimum lightness difference of at least ΔL* {constraints['minimum_lightness_delta_lstar']:.1f} relative to the dominant material color.",
        f"- Avoid same-family background colors whose dominant tone falls within ±{constraints['avoid_lightness_window_lstar']:.1f} L* of the product or that visually blends into the product edges.",
        f"- If the background stays in the same hue family, keep the hue difference within about ±{constraints['same_hue_max_difference_degrees']:.1f}° only when the background is clearly lighter or darker and noticeably less saturated.",
        f"- Prefer restrained MD3 backdrop colors such as: {preferred_text}.",
        f"- Do not center the backdrop on colors too close to the product materials, including: {forbidden_text}.",
        "- Prioritize product edge readability over literal color matching. If needed, desaturate, brighten, darken, or slightly hue-shift the background to preserve clean separation.",
    ))


def additions_from(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = read_json(path)
    if not isinstance(data, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in data
    ):
        raise ValueError("PROMPT_ADDITIONS_INVALID")
    return [entry.strip() for entry in data]


def additions_block(entries: list[str]) -> str | None:
    if not entries:
        return None
    lines = ["User-requested prompt additions accumulated for this product:"]
    lines.extend(f"{index}. {entry}" for index, entry in enumerate(entries, 1))
    return "\n".join(lines)


def build_prompt(args: argparse.Namespace) -> None:
    layout, product_dir = load_layout(Path(args.layout))
    if args.mode == "SKU":
        if not args.master:
            raise ValueError("MASTER_MANIFEST_REQUIRED")
        if Path(args.master).expanduser().resolve() != product_dir / "reusable" / "master.json":
            raise ValueError("MASTER_MANIFEST_PATH_INVALID")
        verify_master(product_dir)
    elif args.master:
        raise ValueError("MASTER_MANIFEST_NOT_ALLOWED_FOR_MASTER_PROMPT")

    addition = (args.additional_prompt or "").strip()
    if addition and not args.redo:
        raise ValueError("ADDITIONAL_PROMPT_REQUIRES_REDO")

    additions_path = product_dir / "reusable" / PROMPT_ADDITIONS_NAME
    additions = additions_from(additions_path)
    if addition:
        additions.append(addition)
        atomic_write(additions_path, additions)

    meta = layout.get("style") == "meta-clear-a"
    parts = [image_prompt(layout)]
    if args.mode == "SKU":
        parts.append("Use the locked master background as the layout and color reference. Preserve its pale blue-white Astryx composition; generate an empty background only." if meta else SKU_EDIT_BLOCK)
    parts.append(information_safe_zone_block(layout))
    if not meta:
        parts.append(background_separation_block(load_palette_payload(product_dir, args.mode), args.mode))
    increment = additions_block(additions)
    if increment:
        parts.append(increment)
    if not meta:
        parts.append(PRODUCT_AREA_POLICY)
    sys.stdout.write("\n\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", choices=("build",))
    parser.add_argument("--mode", required=True, choices=("MASTER", "SKU"))
    parser.add_argument("--layout", required=True)
    parser.add_argument("--master")
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--additional-prompt")
    args = parser.parse_args()

    try:
        build_prompt(args)
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
