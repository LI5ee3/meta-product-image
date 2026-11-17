#!/usr/bin/env python3
"""Preview, create, bind, and verify candidate and SKU artifacts."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_BACKGROUND = SCRIPTS_DIR.parent / "assets" / "meta-clear" / "background.png"
SKU_TARGET_PATTERN = re.compile(r"SKU_VARIANT-([A-Z]+)")


from common import (
    atomic_write,
    load_layout,
    normalize_raster_contract,
    read_json,
    require_matching_raster,
    require_three_four_raster,
    sha256_file,
    verify_master,
)
from compose_image import compose
from measure_text import validate_configuration


def safe_component(value: str, label: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or any(char in value for char in "/\\\x00"):
        raise ValueError(f"{label}_INVALID")
    return value


def load_product(product_dir_arg: str) -> tuple[Path, Path, dict]:
    product_dir = Path(product_dir_arg).expanduser().resolve()
    reusable_dir = product_dir / "reusable"
    layout_path = reusable_dir / "layout.json"
    if not product_dir.is_dir():
        raise ValueError(f"PRODUCT_DIRECTORY_MISSING: {product_dir}")
    layout, product_dir = load_layout(layout_path)
    output_dir = product_dir / "output"
    output_dir.mkdir(exist_ok=True)
    return product_dir, layout_path, layout


def relative_entry(path: Path, product_dir: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(product_dir).as_posix(),
        "sha256": sha256_file(path),
    }


def copy_new(source_arg: str | Path, destination: Path) -> None:
    source = Path(source_arg).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"SOURCE_FILE_MISSING: {source}")
    if destination.exists():
        raise ValueError(f"REFUSE_OVERWRITE: {destination}")
    shutil.copy2(source, destination)


def cleanup(paths: list[Path]) -> None:
    for path in paths:
        if path.is_file():
            path.unlink()


def preview_paths(product_dir: Path, candidate_id: str) -> dict[str, Path]:
    prefix = f"master-candidate-{candidate_id}-preview"
    reusable_dir = product_dir / "reusable"
    return {
        "background": product_dir / f"{prefix}-background.png",
        "product": reusable_dir / f"master-candidate-{candidate_id}-product.png",
        "final": product_dir / f"{prefix}.png",
        "manifest": product_dir / f"{prefix}.json",
    }


def create_preview(args: argparse.Namespace) -> None:
    product_dir, layout_path, layout = load_product(args.product_dir)
    candidate_id = safe_component(args.candidate_id, "CANDIDATE_ID")
    paths = preview_paths(product_dir, candidate_id)
    if any(path.exists() for label, path in paths.items() if label != "product"):
        raise ValueError("PREVIEW_ALREADY_EXISTS")
    layers_existed = paths["product"].exists()
    created = [path for label, path in paths.items() if label != "product"]
    if not layers_existed:
        created.append(paths["product"])
    try:
        product_source = Path(args.product).expanduser().resolve()
        expected_product_hash = layout.get("product_reference", {}).get("sha256")
        if not product_source.is_file() or sha256_file(product_source) != expected_product_hash:
            raise ValueError("MASTER_PRODUCT_REFERENCE_MISMATCH")
        copy_new(DEFAULT_BACKGROUND, paths["background"])
        raster = require_three_four_raster(paths["background"], "MASTER_BACKGROUND")
        compose(
            paths["background"], product_source, layout_path, paths["final"],
            "CANDIDATE", paths["product"], reuse_layers=layers_existed,
        )
        require_matching_raster(paths["product"], raster, "MASTER_PRODUCT_LAYER")
        require_matching_raster(paths["final"], raster, "MASTER_FINAL")
        manifest = {
            "schema": 1,
            "kind": "MASTER_CANDIDATE_PREVIEW",
            "candidate_id": candidate_id,
            "raster": raster,
            "background_sha256": sha256_file(paths["background"]),
            "final_sha256": sha256_file(paths["final"]),
        }
        atomic_write(paths["manifest"], manifest, new=True)
    except Exception:
        cleanup(created)
        raise
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
    print()


def load_preview(product_dir: Path, candidate_id: str) -> tuple[dict, dict[str, Path]]:
    paths = preview_paths(product_dir, candidate_id)
    manifest = read_json(paths["manifest"])
    if manifest.get("kind") != "MASTER_CANDIDATE_PREVIEW" or manifest.get("candidate_id") != candidate_id:
        raise ValueError("PREVIEW_MANIFEST_INVALID")
    raster = normalize_raster_contract(manifest.get("raster"), "PREVIEW_RASTER")
    for label in ("background", "product", "final"):
        require_matching_raster(paths[label], raster, f"PREVIEW_{label.upper()}")
    for label in ("background", "final"):
        expected_hash = manifest.get(f"{label}_sha256")
        if not isinstance(expected_hash, str) or sha256_file(paths[label]) != expected_hash:
            raise ValueError(f"PREVIEW_{label.upper()}_HASH_MISMATCH")
    manifest["raster"] = raster
    return manifest, paths


def discard_preview(args: argparse.Namespace) -> None:
    product_dir, _, _ = load_product(args.product_dir)
    candidate_id = safe_component(args.candidate_id, "CANDIDATE_ID")
    paths = preview_paths(product_dir, candidate_id)
    if not any(path.exists() for path in paths.values()):
        raise ValueError("PREVIEW_MISSING")
    cleanup([path for label, path in paths.items() if label != "product"])
    json.dump({"discarded": True, "candidate_id": candidate_id}, sys.stdout, ensure_ascii=False, indent=2)
    print()


def bind_candidate(args: argparse.Namespace) -> None:
    product_dir, layout_path, _ = load_product(args.product_dir)
    reusable_dir = product_dir / "reusable"
    candidate_id = safe_component(args.candidate_id, "CANDIDATE_ID")
    preview, preview_files = load_preview(product_dir, candidate_id)
    raster = normalize_raster_contract(preview.get("raster"), "PREVIEW_RASTER")
    targets = {
        "background": reusable_dir / "ORIGINAL_MASTER_BACKGROUND.png",
        "product": reusable_dir / "ORIGINAL_MASTER_PRODUCT.png",
        "final": product_dir / "output" / "ORIGINAL_MASTER_FINAL.png",
        "manifest": reusable_dir / "master.json",
    }
    if any(path.exists() for path in targets.values()):
        raise ValueError("MASTER_ALREADY_EXISTS")
    created = list(targets.values())
    try:
        for label in ("background", "product", "final"):
            copy_new(preview_files[label], targets[label])
            require_matching_raster(targets[label], raster, f"MASTER_{label.upper()}")
        master = {
            "schema": 1,
            "state": "BOUND",
            "source_candidate_id": candidate_id,
            "files": {
                "layout": relative_entry(layout_path, product_dir),
                "background": relative_entry(targets["background"], product_dir),
                "product": relative_entry(targets["product"], product_dir),
                "final": relative_entry(targets["final"], product_dir),
            },
            "raster": raster,
        }
        atomic_write(targets["manifest"], master, new=True)
    except Exception:
        cleanup(created)
        raise
    cleanup(list(preview_files.values()))
    json.dump(master, sys.stdout, ensure_ascii=False, indent=2)
    print()


def sku_paths(product_dir: Path, sku_label: str) -> dict[str, Path]:
    reusable_dir = product_dir / "reusable"
    prefix = f"{sku_label}-preview"
    return {
        "background": reusable_dir / "ORIGINAL_MASTER_BACKGROUND.png",
        "product": reusable_dir / f"{sku_label}-product.png",
        "layers": reusable_dir / f"{sku_label}-layers.json",
        "final": product_dir / f"{prefix}.png",
        "manifest": product_dir / f"{prefix}.json",
    }


def sku_letters(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def existing_sku_labels(product_dir: Path) -> list[str]:
    labels = (path.stem for path in (product_dir / "output").glob("SKU_VARIANT-*.png")
              if SKU_TARGET_PATTERN.fullmatch(path.stem))
    return sorted(labels, key=lambda label: (len(label), label))



def resolve_sku_label(product_dir: Path, redo: bool, target: str | None) -> str:
    if target:
        sku_label = safe_component(target, "SKU_LABEL")
        if not redo or not SKU_TARGET_PATTERN.fullmatch(sku_label):
            raise ValueError("SKU_TARGET_IS_AUTOMATIC")
        if not (product_dir / "output" / f"{sku_label}.png").is_file():
            raise ValueError("SKU_REDO_TARGET_MISSING")
        return sku_label
    existing = existing_sku_labels(product_dir)
    if redo:
        if not existing:
            raise ValueError("SKU_REDO_TARGET_MISSING")
        return existing[-1]
    number = 1
    while f"SKU_VARIANT-{sku_letters(number)}" in existing:
        number += 1
    return f"SKU_VARIANT-{sku_letters(number)}"


def create_sku(args: argparse.Namespace) -> None:
    product_dir, layout_path, layout = load_product(args.product_dir)
    master = verify_master(product_dir)
    expected_raster = normalize_raster_contract(master.get("raster"), "MASTER_RASTER")
    redo = bool(args.redo)
    sku_label = resolve_sku_label(product_dir, redo, args.target)
    paths = sku_paths(product_dir, sku_label)
    previous = read_json(paths["manifest"]) if redo else {}
    if redo and (set(previous.get("files", {})) != {"background", "product", "final"} or "configuration" not in previous):
        raise ValueError("SKU_MANIFEST_INVALID")
    configuration = args.configuration
    if configuration is None:
        configuration = previous["configuration"] if redo else layout["configuration"]
    if configuration is not None:
        configuration = validate_configuration(configuration)
        if not layout.get("elements", {}).get("CONFIGURATION_RECT"):
            raise ValueError("MASTER_CONFIGURATION_MISSING")

    layers_exist = paths["product"].exists()
    if args.product:
        product_source = Path(args.product).expanduser().resolve()
        product_hash = sha256_file(product_source)
    else:
        if redo and not layers_exist:
            raise ValueError("CACHED_PRODUCT_LAYER_INCOMPLETE")
        if layers_exist:
            product_hash = read_json(paths["layers"])["source_sha256"]
        else:
            if paths["layers"].exists():
                raise ValueError("CACHED_PRODUCT_LAYER_INCOMPLETE")
            product_hash = layout["product_reference"]["sha256"]
        product_source = product_dir / "reusable" / "ORIGINAL_MASTER_PRODUCT.png"

    final_output = product_dir / "output" / f"{sku_label}.png"
    if final_output.exists() and not redo:
        raise ValueError("SKU_ALREADY_EXISTS")
    # Stage a supplied product separately, so failed redos leave the old cache intact.
    update_product = bool(args.product) or not layers_exist
    staged = {}
    product_layer = paths["product"]
    if update_product:
        product_layer = paths["product"].with_name(f".{sku_label}-product-next.png")
        staged[paths["product"]] = product_layer
        staged[paths["layers"]] = paths["layers"].with_name(f".{sku_label}-layers-next.json")
    staged[final_output] = paths["final"]
    staged[paths["manifest"]] = paths["manifest"].with_name(f".{sku_label}-manifest-next.json")
    cleanup(list(staged.values()))
    backups = {}
    published = []
    try:
        if update_product and not args.product:
            copy_new(product_source, product_layer)
        compose(
            paths["background"], product_source, layout_path, paths["final"],
            "SKU_PREVIEW", product_layer,
            configuration if configuration != layout["configuration"] else None,
            reuse_layers=not bool(args.product),
        )
        require_matching_raster(product_layer, expected_raster, "SKU_PRODUCT_LAYER")
        require_matching_raster(paths["final"], expected_raster, "SKU_FINAL")
        if update_product:
            atomic_write(staged[paths["layers"]], {"source_sha256": product_hash}, new=True)
        manifest = {
            "kind": "SKU_VARIANT",
            "sku_label": sku_label,
            "configuration": configuration,
            "files": {
                "background": relative_entry(paths["background"], product_dir),
                "product": relative_entry(product_layer, product_dir) | {
                    "path": paths["product"].relative_to(product_dir).as_posix(),
                },
                "final": relative_entry(paths["final"], product_dir) | {
                    "path": final_output.relative_to(product_dir).as_posix(),
                },
            },
            "raster": expected_raster,
        }
        atomic_write(staged[paths["manifest"]], manifest, new=True)
        # Publish the image, cache and redo record together; restore them on failure.
        for target, pending in staged.items():
            if target.exists():
                backup = target.with_name(f".{target.name}.previous")
                shutil.copy2(target, backup)
                backups[target] = backup
            pending.replace(target)
            published.append(target)
    except Exception:
        for target in reversed(published):
            if target in backups:
                backups[target].replace(target)
            else:
                target.unlink()
        raise
    finally:
        cleanup([*staged.values(), *backups.values()])
    cleanup([product_dir / f"{sku_label}-preview-background.png"])
    json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("preview")
    preview.add_argument("--product", required=True)
    preview.add_argument("--product-dir", required=True)
    preview.add_argument("--candidate-id", required=True)
    preview.set_defaults(handler=create_preview)
    discard = subparsers.add_parser("discard-preview")
    discard.add_argument("--product-dir", required=True)
    discard.add_argument("--candidate-id", required=True)
    discard.set_defaults(handler=discard_preview)
    bind = subparsers.add_parser("bind")
    bind.add_argument("--product-dir", required=True)
    bind.add_argument("--candidate-id", required=True)
    bind.set_defaults(handler=bind_candidate)
    sku = subparsers.add_parser("sku")
    sku.add_argument("--product", help="omit to reuse the master product, or the target SKU product on redo")
    sku.add_argument("--configuration", help="replace only the memory/storage configuration on the locked layout")
    sku.add_argument("--product-dir", required=True)
    sku.add_argument("--redo", action="store_true")
    sku.add_argument("--target")
    sku.set_defaults(handler=create_sku)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
