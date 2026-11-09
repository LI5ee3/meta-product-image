"""Shared file helpers for product-image masters and SKU variants."""

import hashlib
import json
from pathlib import Path

from PIL import Image


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"FILE_UNREADABLE: {path}: {exc}") from exc
    return digest.hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON_UNREADABLE: {path}: {exc}") from exc


def atomic_write(path: Path, data, *, new: bool = False) -> None:
    if new and path.exists():
        raise ValueError(f"REFUSE_OVERWRITE: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def raster_info(path: Path) -> dict[str, int | str]:
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            mode = image.mode
    except OSError as exc:
        raise ValueError(f"RASTER_UNREADABLE: {path}: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"RASTER_DIMENSIONS_INVALID: {path}")
    return {
        "width": width,
        "height": height,
        "ratio": "3:4" if width * 4 == height * 3 else f"{width}:{height}",
        "mode": mode,
    }


def require_three_four_raster(path: Path, label: str) -> dict[str, int | str]:
    info = raster_info(path)
    width = int(info["width"])
    height = int(info["height"])
    if width * 4 != height * 3:
        raise ValueError(f"{label}_NOT_3_4: got {width}x{height}")
    return info


def normalize_raster_contract(value, label: str) -> dict[str, int | str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}_INVALID")
    try:
        width = int(value["width"])
        height = int(value["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}_INVALID") from exc
    if width <= 0 or height <= 0 or width * 4 != height * 3:
        raise ValueError(f"{label}_INVALID")
    return {"width": width, "height": height, "ratio": "3:4"}


def require_matching_raster(
    path: Path,
    expected,
    label: str,
) -> dict[str, int | str]:
    contract = normalize_raster_contract(expected, "EXPECTED_RASTER")
    actual = require_three_four_raster(path, label)
    if (
        int(actual["width"]) != int(contract["width"])
        or int(actual["height"]) != int(contract["height"])
    ):
        raise ValueError(
            f"{label}_RASTER_MISMATCH: expected "
            f"{contract['width']}x{contract['height']}, got "
            f"{actual['width']}x{actual['height']}"
        )
    return actual


def verify_information_assets(layout: dict, reusable_dir: Path) -> None:
    # Keep reading saved Meta layouts under their original style identifier.
    if layout.get("style") not in {"meta", "meta-clear-a"}:
        raise ValueError("UNSUPPORTED_LAYOUT_STYLE: create a Meta master in a new output root")
    try:
        elements = layout["elements"]
        assets = layout["information_assets"]
        expected = {"overlay": elements["INFORMATION_OVERLAY_RECT"]["asset"]}
    except (KeyError, TypeError) as exc:
        raise ValueError("INFORMATION_ASSETS_INVALID") from exc
    if set(assets) != set(expected):
        raise ValueError("INFORMATION_ASSET_SET_MISMATCH")
    for label, name in expected.items():
        try:
            entry = assets[label]
            path = (reusable_dir / entry["path"]).resolve()
        except (KeyError, TypeError) as exc:
            raise ValueError(f"INFORMATION_ASSET_INVALID: {label}") from exc
        if path.parent != reusable_dir.resolve() or path != (reusable_dir / name).resolve() or not path.is_file():
            raise ValueError(f"INFORMATION_ASSET_PATH_MISMATCH: {label}")
        if sha256_file(path) != entry.get("sha256"):
            raise ValueError(f"INFORMATION_ASSET_HASH_MISMATCH: {label}")


def verify_master(product_dir: Path) -> dict:
    reusable = product_dir / "reusable"
    manifest = read_json(reusable / "master.json")
    if manifest.get("state") != "BOUND" or not isinstance(manifest.get("files"), dict):
        raise ValueError("MASTER_NOT_BOUND")
    expected = {
        "layout": reusable / "layout.json",
        "background": reusable / "ORIGINAL_MASTER_BACKGROUND.png",
        "product": reusable / "ORIGINAL_MASTER_PRODUCT.png",
        "final": product_dir / "output" / "ORIGINAL_MASTER_FINAL.png",
    }
    for label, expected_path in expected.items():
        entry = manifest["files"].get(label)
        try:
            path = (product_dir / entry["path"]).resolve()
            expected_hash = entry["sha256"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"MANIFEST_ENTRY_INVALID: {label}") from exc
        if path != expected_path or not path.is_file():
            raise ValueError(f"MANIFEST_PATH_MISMATCH: {label}")
        if sha256_file(path) != expected_hash:
            raise ValueError(f"MANIFEST_HASH_MISMATCH: {label}")

    raster_contract = normalize_raster_contract(manifest.get("raster"), "MASTER_RASTER")
    manifest = dict(manifest)
    manifest["raster"] = raster_contract

    for label in ("background", "product", "final"):
        require_matching_raster(expected[label], raster_contract, f"MASTER_{label.upper()}")
    return manifest
