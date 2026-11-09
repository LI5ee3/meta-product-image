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


def require_three_four_raster(path: Path, label: str) -> dict[str, int | str]:
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except OSError as exc:
        raise ValueError(f"RASTER_UNREADABLE: {path}: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"RASTER_DIMENSIONS_INVALID: {path}")
    if width * 4 != height * 3:
        raise ValueError(f"{label}_NOT_3_4: got {width}x{height}")
    return {"width": width, "height": height, "ratio": "3:4"}



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
    if actual != contract:
        raise ValueError(
            f"{label}_RASTER_MISMATCH: expected "
            f"{contract['width']}x{contract['height']}, got "
            f"{actual['width']}x{actual['height']}"
        )
    return actual


def verify_information_assets(layout: dict, reusable_dir: Path) -> None:
    if layout.get("style") != "meta":
        raise ValueError("UNSUPPORTED_LAYOUT_STYLE: create a Meta master in a new output root")
    try:
        name = layout["elements"]["INFORMATION_OVERLAY_RECT"]["asset"]
        assets = layout["information_assets"]
    except (KeyError, TypeError) as exc:
        raise ValueError("INFORMATION_ASSETS_INVALID") from exc
    if set(assets) != {"overlay"}:
        raise ValueError("INFORMATION_ASSET_SET_MISMATCH")
    try:
        entry = assets["overlay"]
        path = (reusable_dir / entry["path"]).resolve()
    except (KeyError, TypeError) as exc:
        raise ValueError("INFORMATION_ASSET_INVALID: overlay") from exc
    if path.parent != reusable_dir.resolve() or path != (reusable_dir / name).resolve() or not path.is_file():
        raise ValueError("INFORMATION_ASSET_PATH_MISMATCH: overlay")
    if sha256_file(path) != entry.get("sha256"):
        raise ValueError("INFORMATION_ASSET_HASH_MISMATCH: overlay")


def load_layout(path: Path) -> tuple[dict, Path]:
    path = path.expanduser().resolve()
    if path.name != "layout.json" or path.parent.name != "reusable":
        raise ValueError("LAYOUT_PATH_INVALID: expected <product>/reusable/layout.json")
    layout = read_json(path)
    product_dir = path.parent.parent
    if layout.get("product_directory") != str(product_dir):
        raise ValueError("PRODUCT_DIRECTORY_MISMATCH")
    verify_information_assets(layout, path.parent)
    return layout, product_dir


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
    if set(manifest["files"]) != set(expected):
        raise ValueError("MASTER_ASSET_SET_MISMATCH")
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
    manifest["raster"] = raster_contract

    for label in ("background", "product", "final"):
        require_matching_raster(expected[label], raster_contract, f"MASTER_{label.upper()}")
    return manifest
