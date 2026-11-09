#!/usr/bin/env python3
"""Build deterministic Image Gen prompts without persistent attempt state."""

import argparse
import sys
from pathlib import Path

from common import atomic_write, load_layout, read_json, verify_master


SKILL_DIR = Path(__file__).resolve().parent.parent
IMAGE_PROMPT_REFERENCE = SKILL_DIR / "references" / "image-gen-prompt.md"
PROMPT_ADDITIONS_NAME = "prompt-additions.json"


def image_prompt() -> str:
    try:
        prompt = IMAGE_PROMPT_REFERENCE.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise ValueError(
            f"REFERENCE_UNREADABLE: {IMAGE_PROMPT_REFERENCE}: {exc}"
        ) from exc
    if not prompt:
        raise ValueError("IMAGE_PROMPT_EMPTY")
    return prompt


def information_safe_zone_block(layout: dict) -> str:
    zones = layout.get("clear_zones")
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


def additions_from(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = read_json(path)
    if not isinstance(data, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in data
    ):
        raise ValueError("PROMPT_ADDITIONS_INVALID")
    return [entry.strip() for entry in data]


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

    parts = [image_prompt()]
    if args.mode == "SKU":
        parts.append("Use the locked master background as the layout and color reference. Preserve its pale blue-white Astryx composition; generate an empty background only.")
    parts.append(information_safe_zone_block(layout))

    additions_path = product_dir / "reusable" / PROMPT_ADDITIONS_NAME
    additions = additions_from(additions_path)
    if addition:
        additions.append(addition)
        atomic_write(additions_path, additions)

    if additions:
        lines = ["User-requested prompt additions accumulated for this product:"]
        lines.extend(f"{index}. {entry}" for index, entry in enumerate(additions, 1))
        parts.append("\n".join(lines))
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
