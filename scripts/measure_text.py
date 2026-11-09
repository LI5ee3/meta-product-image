#!/usr/bin/env python3
"""Measure and cache the Meta layout, including three sourced Russian features."""

import argparse
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from common import atomic_write, read_json, sha256_file

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_DEFAULT = ASSETS / "Rubik[wght].ttf"
FONT_WEIGHT_DEFAULT = 600
INK = (24, 42, 53, 255)
SECONDARY = (71, 91, 105, 255)
ACCENT = (36, 98, 131, 255)
PRODUCT_BOX = (64, 308, 640, 478)
VERSION_MIN_SIZE = 28
FEATURE_HEADLINE_MIN_SIZE = 26
FEATURE_LABEL_MIN_SIZE = 22
CONFIGURATION_MIN_SIZE = 32


def load_font(font_path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(font_path), size)
    try:
        axes = font.get_variation_axes()
    except OSError:
        axes = []
    if axes:
        if len(axes) != 1 or not axes[0]["minimum"] <= weight <= axes[0]["maximum"]:
            raise ValueError("FONT_VARIATION_UNSUPPORTED")
        font.set_variation_by_axes([weight])
    return font


def mask_signature(font: ImageFont.FreeTypeFont, text: str) -> tuple:
    mask = font.getmask(text, mode="L")
    return mask.size, mask.getbbox(), bytes(mask)


def check_glyph_coverage(font_path: Path, weight: int, *texts: str) -> None:
    font = load_font(font_path, 96, weight)
    missing = mask_signature(font, "\U0010ffff")
    absent = sorted({c for text in texts for c in text if not c.isspace()
                     and mask_signature(font, c) == missing})
    if absent:
        raise ValueError(f"GLYPH_MISSING: {''.join(absent)}")


def image_metadata(path: Path) -> dict:
    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        box = rgba.getchannel("A").getbbox() or (0, 0, *im.size)
        return {"path": str(path), "sha256": sha256_file(path), "width": im.width,
                "height": im.height, "mode": im.mode, "format": im.format,
                "visible_bbox": dict(zip(("left", "top", "right", "bottom"), box))}


def visible_logo(path: Path) -> tuple[Image.Image, dict]:
    with Image.open(path) as source:
        logo = source.convert("RGBA")
    bbox = logo.getchannel("A").getbbox()
    if not bbox:
        raise ValueError("LOGO_EMPTY")
    return logo.crop(bbox), {"source_path": str(path), "sha256": sha256_file(path),
                             "visible_bbox": list(bbox)}


def resolve_product_directory(output_root: Path, complete_name: str) -> Path:
    if (not complete_name or complete_name in {".", ".."}
            or any(c in complete_name for c in '/\\\x00<>:"|?*')
            or complete_name.endswith((" ", "."))):
        raise ValueError("PRODUCT_FOLDER_NAME_INVALID: use one exact folder name")
    root = output_root.expanduser().resolve()
    product_dir = (root / complete_name).resolve()
    if product_dir.parent != root:
        raise ValueError("PRODUCT_FOLDER_OUTSIDE_ROOT")
    return product_dir


def normalized_rect(rect, width: int, height: int) -> dict:
    x, y, right, bottom = rect
    return {"x": x/width, "y": y/height, "w": (right-x)/width, "h": (bottom-y)/height}


def text_width(font, text: str, tracking: float = 0) -> float:
    return font.getlength(text) + max(0, len(text)-1)*tracking


def wrap_two_lines(text: str, font, width: float, tracking: float = 0) -> list[str] | None:
    if text_width(font, text, tracking) <= width:
        return [text]
    words = text.split(" ")
    candidates = []
    for i in range(1, len(words)):
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        lengths = [text_width(font, line, tracking) for line in lines]
        if max(lengths) <= width:
            candidates.append((abs(lengths[0]-lengths[1]), lines))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def fit_title(text: str, font_path: Path, scale: float, weight: int,
              manual_lines: list[str] | None = None):
    protected = re.sub(r"(\S+) (\d+[A-Za-z]*)(?= |$)", lambda m: m[1]+"\u00a0"+m[2], text)
    sizes = [max(1, round(size*scale)) for size in (68, 64, 60, 56)]
    sizes.extend(range(sizes[-1]-1, 0, -1))
    for size in sizes:
        font = load_font(font_path, size, weight)
        tracking = -.035*font.size
        lines = manual_lines or wrap_two_lines(protected, font, 672*scale, tracking)
        if lines and all(text_width(font, line, tracking) <= 672*scale for line in lines):
            return lines, font
    raise ValueError("TITLE_CANNOT_FIT: text exceeds the canvas even at one pixel")


def load_features(path: Path) -> list[dict]:
    features = read_json(path)
    if not isinstance(features, list) or len(features) != 3:
        raise ValueError("FEATURES_REQUIRE_EXACTLY_THREE")
    for f in features:
        if not isinstance(f, dict) or any(not isinstance(f.get(k), str) or not f[k].strip()
                                          for k in ("icon", "headline", "label", "source_url")):
            raise ValueError("FEATURE_FIELDS_REQUIRED: icon, headline, label, source_url")
        if any(c in f["headline"]+f["label"] for c in "\r\n"):
            raise ValueError("FEATURE_TEXT_SINGLE_INPUT_LINE: wrapping is automatic")
        if not re.fullmatch(r"[a-z0-9-]+", f["icon"]) or not (ASSETS/"lucide"/(f["icon"]+".png")).is_file():
            raise ValueError(f"LUCIDE_ICON_UNAVAILABLE: {f['icon']}")
        source = urlparse(f["source_url"])
        if source.scheme != "https" or not source.hostname:
            raise ValueError("FEATURE_OFFICIAL_SOURCE_REQUIRED: use an official HTTPS product page")
    return features


def validate_configuration(text: str) -> str:
    text = text.strip()
    if not text or any(c in text for c in "\r\n"):
        raise ValueError("CONFIGURATION_SINGLE_LINE_REQUIRED")
    return text


def draw_text_line(image, text, x, y, area_width, font, color,
                   align="center", line_height=1.08, spacing=0):
    tracking = spacing*font.size
    length = text_width(font, text, tracking)
    if length > area_width+1:
        raise ValueError(f"TEXT_OVERFLOW: {text}")
    left = x+(area_width-length)/2 if align == "center" else x+area_width-length
    ascent, descent = font.getmetrics()
    baseline = y+(font.size*line_height-ascent-descent)/2+ascent
    draw = ImageDraw.Draw(image)
    for i, c in enumerate(text):
        offset = font.getlength(text[:i+1])-font.getlength(c)+i*tracking
        draw.text((left+offset, baseline), c, font=font, fill=color, anchor="ls")
    return normalized_rect((left, y, left+length, y+font.size*line_height), *image.size)


def configuration_overlay(layout: dict, reusable: Path, text: str) -> Image.Image:
    text = validate_configuration(text)
    rect = layout.get("elements", {}).get("CONFIGURATION_RECT")
    if layout.get("style") != "meta" or not rect:
        raise ValueError("MASTER_CONFIGURATION_MISSING: the master has no configuration line")
    font_path = Path(layout["font"]["path"])
    check_glyph_coverage(font_path, 500, text)
    with Image.open(reusable/layout["elements"]["INFORMATION_OVERLAY_RECT"]["asset"]) as source:
        overlay = source.convert("RGBA")
    width, height = overlay.size
    font = load_font(font_path, round(rect["h"]*height/1.2), 500)
    top = rect["y"]*height
    # Clear only this isolated line; keep all other cached pixels intact.
    overlay.paste((0, 0, 0, 0), (0, math.floor(top)-2, width, math.ceil(top+rect["h"]*height)+2))
    draw_text_line(overlay, text, width/16, top, width*7/8, font, INK, line_height=1.2)
    return overlay


def prepare_layout(args: argparse.Namespace) -> dict:
    if args.canvas_height <= 0 or args.canvas_height % 4:
        raise ValueError("CANVAS_HEIGHT_INVALID: use a positive multiple of 4")
    width, height = args.canvas_height*3//4, args.canvas_height
    scale = height/1024
    font_path = Path(args.font_path).expanduser().resolve()
    product_dir = resolve_product_directory(Path(args.output_root), args.complete_name)
    reusable = product_dir/"reusable"
    if (reusable/"layout.json").exists():
        raise ValueError(f"REFUSE_REMEASURE: reuse existing {reusable/'layout.json'}")
    features = load_features(Path(args.features))
    title = args.title or args.complete_name
    manual = None
    if args.title_lines is not None or args.title_line_1 is not None or args.title_line_2 is not None:
        if args.title or not args.title_lines or not args.title_line_1:
            raise ValueError("TITLE_INPUT_CONFLICT: use --title or complete manual lines")
        manual = [args.title_line_1] + ([args.title_line_2] if args.title_lines == 2 else [])
        if any(not line for line in manual) or (args.title_lines == 1 and args.title_line_2):
            raise ValueError("TITLE_LINE_COUNT_MISMATCH")
        title = " ".join(manual)
    configuration = args.configuration
    if configuration is not None:
        configuration = validate_configuration(configuration)
    texts = [title, args.version or "", configuration or "", *[f[k] for f in features for k in ("headline", "label")]]
    for weight in {400, 500, args.font_weight}:
        check_glyph_coverage(font_path, weight, *texts)
    title_lines, title_font = fit_title(title, font_path, scale, args.font_weight, manual)
    # Supporting text follows the title only until its readability floor.
    supporting_scale = min(1, title_font.size/(56*scale))
    product_meta = image_metadata(Path(args.product_reference).expanduser().resolve())
    logo, logo_meta = visible_logo(Path(args.logo).expanduser().resolve())
    overlay = Image.new("RGBA", (width, height))
    text_rects = []

    def text_line(text, x, y, area_width, font, color, align="center", line_height=1.08, spacing=0):
        rect = draw_text_line(overlay, text, x*scale, y*scale, area_width*scale,
                              font, color, align, line_height, spacing)
        text_rects.append({"text": text, **rect})
        return rect

    lx, ly, lw, lh = (48, 42, 174, 62) if logo.width/logo.height > 2 else (48, 36, 76, 76)
    logo_scale = min(lw*scale/logo.width, lh*scale/logo.height)
    logo = logo.resize((round(logo.width*logo_scale), round(logo.height*logo_scale)), Image.Resampling.LANCZOS)
    logo_xy = (round(lx*scale+(lw*scale-logo.width)/2), round(ly*scale+(lh*scale-logo.height)/2))
    overlay.alpha_composite(logo, logo_xy)
    logo_rect = normalized_rect((*logo_xy, logo_xy[0]+logo.width, logo_xy[1]+logo.height), width, height)
    version_rect = None
    if args.version:
        version_font = load_font(font_path, max(math.ceil(VERSION_MIN_SIZE*scale), round(34*scale*supporting_scale)), 400)
        version_rect = text_line(args.version, 264, 58, 456, version_font, SECONDARY, "right", 1.2)
    title_rects = [text_line(line, 48, 156+i*title_font.size/scale*1.08, 672,
                             title_font, INK, spacing=-.035) for i, line in enumerate(title_lines)]
    x, y, w, h = PRODUCT_BOX
    configuration_rect = None
    if configuration:
        configuration_font = load_font(font_path, max(math.ceil(CONFIGURATION_MIN_SIZE*scale), round(36*scale*supporting_scale)), 500)
        configuration_top = 156+len(title_lines)*title_font.size/scale*1.08+12
        configuration_rect = text_line(configuration, 48, configuration_top, 672, configuration_font, INK, line_height=1.2)
        product_top = max(y, configuration_top+configuration_font.size/scale*1.2+20)
        h, y = y+h-product_top, product_top

    column_width = 664/3
    label_font = load_font(font_path, math.ceil(FEATURE_LABEL_MIN_SIZE*scale), 400)
    labels = [wrap_two_lines(f["label"], label_font, (column_width-16)*scale) for f in features]
    if any(lines is None for lines in labels):
        raise ValueError("FEATURE_LABEL_OVERFLOW: repartition the same official wording before selecting another fact")
    label_height = label_font.size/scale*1.18
    label_area_height = max(map(len, labels))*label_height
    for size in (28, 26):
        headline_font = load_font(font_path, max(math.ceil(FEATURE_HEADLINE_MIN_SIZE*scale), round(size*scale*supporting_scale)), 500)
        headlines = [wrap_two_lines(f["headline"], headline_font, (column_width-16)*scale,
                                    -.025*headline_font.size) for f in features]
        if any(lines is None for lines in headlines):
            continue
        headline_height = headline_font.size/scale*1.12
        headline_area_height = max(map(len, headlines))*headline_height
        panel_height = 20+30+12+headline_area_height+7+label_area_height+20
        if 812+panel_height <= 1014:
            break
    else:
        error = "FEATURE_HEADLINE_OVERFLOW" if any(lines is None for lines in headlines) else "FEATURE_PANEL_OVERFLOW"
        raise ValueError(error + ": repartition the same official wording; do not truncate or shrink below the minimum")
    panel_box = tuple(round(v*scale) for v in (48, 812, 720, 812+panel_height))
    panel = Image.new("RGBA", overlay.size)
    ImageDraw.Draw(panel).rounded_rectangle(panel_box, radius=round(28*scale), fill=(255,255,255,217))
    shadow = Image.new("RGBA", overlay.size)
    shifted = (panel_box[0], panel_box[1]+round(10*scale), panel_box[2], panel_box[3]+round(10*scale))
    ImageDraw.Draw(shadow).rounded_rectangle(shifted, radius=round(28*scale), fill=(69,103,135,10))
    overlay = Image.alpha_composite(overlay, shadow.filter(ImageFilter.GaussianBlur(15*scale)))
    overlay = Image.alpha_composite(overlay, panel)
    for i, (f, headline_lines, lines) in enumerate(zip(features, headlines, labels)):
        left = 52+i*column_width
        if i:
            draw = ImageDraw.Draw(overlay)
            draw.line(((round(left*scale), round(832*scale)), (round(left*scale), round((792+panel_height)*scale))), fill=(182,201,215,102), width=max(1,round(scale)))
        with Image.open(ASSETS/"lucide"/(f["icon"]+".png")) as source:
            mask = source.getchannel("A").resize((round(30*scale),)*2, Image.Resampling.LANCZOS)
        icon = Image.new("RGBA", mask.size, ACCENT)
        icon.putalpha(mask)
        overlay.alpha_composite(icon, (round((left+(column_width-30)/2)*scale), round(832*scale)))
        headline_y = 874+(headline_area_height-len(headline_lines)*headline_height)/2
        label_y = 874+headline_area_height+7+(label_area_height-len(lines)*label_height)/2
        for j, line in enumerate(headline_lines):
            text_line(line, left+8, headline_y+j*headline_height, column_width-16, headline_font, INK, line_height=1.12, spacing=-.025)
        for j, line in enumerate(lines):
            text_line(line, left+8, label_y+j*label_height, column_width-16, label_font, SECONDARY, line_height=1.18)

    reusable.mkdir(parents=True, exist_ok=True)
    (product_dir/"output").mkdir(exist_ok=True)
    overlay.save(reusable/"information-overlay.png")
    elements = {"LOGO_RECT": logo_rect, "TITLE_LINE_RECT": title_rects,
                "PRODUCT_RECT": normalized_rect((x, y, x+w, y+h), 768, 1024),
                "FEATURE_PANEL_RECT": normalized_rect((48, 812, 720, 812+panel_height), 768, 1024),
                "INFORMATION_OVERLAY_RECT": {"x":0,"y":0,"w":1,"h":1,"asset":"information-overlay.png"}}
    if version_rect:
        elements["VERSION_TEXT_RECT"] = version_rect
    if configuration_rect:
        elements["CONFIGURATION_RECT"] = configuration_rect
    report = {"schema":2, "style":"meta", "product_directory":str(product_dir),
              "canvas":{"width":width,"height":height,"ratio":"3:4","purpose":"logical layout only"},
              "font":{"path":str(font_path),"name":"Rubik Variable","weight":args.font_weight},
              "product_reference":product_meta, "logo":logo_meta, "complete_name":args.complete_name,
              "rendered_title":"\n".join(title_lines), "title_lines":title_lines, "title_line_count":len(title_lines),
              "title_font_size":title_font.size, "version":args.version, "configuration":configuration, "features":features,
              "feature_headline_lines":headlines, "feature_label_lines":labels, "elements":elements, "text_rects":text_rects,
              "information_assets":{"overlay":{"path":"information-overlay.png","sha256":sha256_file(reusable/"information-overlay.png")}},
              "clear_zones":[{"label":"TOP_INFORMATION",**normalized_rect((32,20,736,y-4),768,1024)},
                             {"label":"BOTTOM_FEATURES",**normalized_rect((32,800,736,1024),768,1024)}]}
    atomic_write(reusable/"layout.json", report, new=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complete-name", required=True)
    parser.add_argument("--title", help="display name; defaults to complete product name")
    parser.add_argument("--title-lines", type=int, choices=(1,2))
    parser.add_argument("--title-line-1")
    parser.add_argument("--title-line-2")
    parser.add_argument("--version")
    parser.add_argument("--configuration", help='optional user-supplied configuration, e.g. "8/256 ГБ"')
    parser.add_argument("--features", required=True, help="JSON array of exactly three sourced Russian features")
    parser.add_argument("--product-reference", required=True)
    parser.add_argument("--logo", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--canvas-height", type=int, default=2048)
    parser.add_argument("--font-path", default=str(FONT_DEFAULT))
    parser.add_argument("--font-weight", type=int, default=FONT_WEIGHT_DEFAULT)
    try:
        report = prepare_layout(parser.parse_args())
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
