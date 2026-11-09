---
name: meta-product-image
description: "Create reusable 3:4 Meta Astryx-inspired product images with exact local product/Logo compositing, automatic two-line titles and three sourced Russian selling points with Lucide icons. Use for product main images, user-locked masters, color SKU variants and redos."
---

# Meta Product Image

Use the user-approved Meta “清透层叠” design in [DESIGN.md](DESIGN.md).
Invoke this Skill with $meta-product-image.

## Default workflow

USER_PROVIDED_URL -> OFFICIAL_FACTS -> MEASURE_ONCE -> LOCAL_PREVIEW -> USER_LOCK -> SKU

Reuse the bundled approved background and local information overlay. The default workflow needs no Image Gen call, browser or HTML renderer.
Generate a new background only when the user requests one.

## 1. Inputs and official facts

Before creating a new master or researching new facts, require an official product-page URL explicitly supplied by the user for this product (a URL already supplied in this conversation counts). If absent, stop immediately and reply: “请提供该产品的官网页面网址；未提供网址，已停止制作。” Do not search, guess a URL, use an example URL as authorization, extract facts, measure or generate a new master while it is missing. Reusing a bound master for color or memory/storage changes uses its previously approved facts and sources; do not repeat source collection or ask for the same URL again.

For a new product obtain the authoritative product PNG/WEBP, original Logo, complete model name, optional agreed display name, optional version text, optional user-supplied configuration, and three useful facts from the user-specified official page for this exact model/region. Do not select a memory/storage configuration from the website on the user's behalf.

The version example is “Глобальная версия”. Do not infer that inventory is global from the design example.

Read only the product-page URL(s) specified by the user for selling-point research. Never independently search for or select another source page. If the supplied page is inaccessible, not official, mismatches the model, or lacks enough reliable facts, stop and ask the user to supply a suitable official URL; do not find a replacement yourself. Retain the supplied URL and material conditions. Translate to short Russian headlines and explanations. Battery duration must retain “До” and its mode; distinguish typical capacity, charging limits and camera modes where relevant. Never borrow facts from another model or invent missing specifications.

Write a UTF-8 JSON array of exactly three entries:
- icon: a bundled Lucide name;
- headline: one compact line, usually a number or benefit;
- label: a short Russian explanation, automatically fitted to at most two lines;
- source_url: the official HTTPS product-page URL explicitly specified by the user;
- source_note: optional supporting fact, qualification or verification note.

See [references/features-example.json](references/features-example.json). Its facts apply only to OnePlus Watch 4, never to another product.
Bundled icons: watch, activity, battery, camera, monitor, volume-2. Match the icon to the fact. Additional icons must come from official Lucide SVGs with matching transparent PNGs and license; see assets/lucide/README.md. Do not improvise icons or use emoji.

Use normal Russian case, decimal commas and consistent units such as мА·ч, Гц and Мп. Use ASCII double quote for inches rather than unsupported U+2033. Do not add unrequested prices or promotional claims.

## 2. Measure once

Run from the Skill directory using Python with Pillow:

    python scripts/measure_text.py --complete-name "<complete model name>" --product-reference "<product.png>" --logo "<logo.png>" --features "<features.json>" --version "Глобальная версия" --output-root "<output root>"

Omit --version when not supplied. The complete name is the default title. Use --title "<display name>" only when supplied or agreed; do not silently remove model information.

When the user supplies a configuration, append --configuration "8/256 ГБ" (using their actual value). It is a separate centered line below the final title line and above the product, never part of the title or one of the three benefits. It stays on one line, uses weight 500 and 36 px with a 32 px minimum on the 768×1024 reference canvas. Keep 12 px after the title and at least 20 px before the product box. A two-line title pushes the product box down while preserving its bottom edge; contain scaling keeps the full product visible. With no configuration, omit the line and retain the original layout.

The script creates <output root>/<complete model name>/reusable/layout.json and information-overlay.png.
It measures the actual font, chooses one or two title lines, and keeps model-number groups together. If the normal title sizes do not fit, automatically reduce the font size until the full name fits; do not ask the user to shorten it. No ellipsis, horizontal squeezing or silent truncation. Version and feature text may follow the title's reduction only down to their readability floors: version 28 px, feature headline 32 px, feature explanation 24 px on the 768×1024 reference canvas (56/64/48 px on the 1536×2048 logical canvas). Scale these floors with the canvas; never shrink below them to accommodate a title. Preserve hierarchy through title placement and weight 600, feature headline weight 500, and secondary-colored version/explanation weight 400.

Exact manually requested lines remain supported by --title-lines 1|2, --title-line-1 and optional --title-line-2. Do not combine them with --title.

Three features use equal columns, bounded headline sizes and word-boundary wrapping of explanations to at most two lines. Overlong text fails before saving the layout. At the minimum size, shorten a selling-point phrase without changing its fact or qualification, then rerun; never lower the floor. If supplied version text cannot fit, request a concise version label without changing its meaning.

The logical canvas is 1536×2048. Products use one centered contain box measured from source Alpha bounds. Preserve proportions and all visible artwork; watches, phones, tablets and different source margins use the same rule.

The cached overlay contains the Logo, title, optional version and configuration, three icons and their text on a translucent rounded panel. Rubik weights: title 600, configuration and feature headline 500, other text 400. Its SHA-256 is checked. Color SKUs reuse it unchanged; configuration SKUs replace only the configuration line in a temporary copy, preserving the master and every other cached pixel.

Reuse a current Meta layout. Never remeasure or overwrite a product directory silently. To redesign a locked product, use a new output root and preserve its master/SKUs.

## 3. Master preview and lock

    python scripts/artifact_flow.py preview --product "<product.png>" --product-dir "<PRODUCT_DIRECTORY>" --candidate-id a

The default background is assets/meta-clear/background.png. Output uses its actual raster, not a forced logical size. The exact product and Logo remain local. The product has no cast shadow.
Show the completed full-size preview and stop; do not automatically bind it.

Only after the explicit instruction “锁定母版”:

    python scripts/artifact_flow.py bind --product-dir "<PRODUCT_DIRECTORY>" --candidate-id a

This binds the inspected pixels and actual raster to output/ORIGINAL_MASTER_FINAL.png and reusable/master.json. Selecting a design direction alone is not locking a product master.

## 4. Color and configuration SKU reuse

After locking, use another color of the same model/specification:

    python scripts/artifact_flow.py sku --product "<sku.png>" --product-dir "<PRODUCT_DIRECTORY>"

It reuses the locked background, overlay and raster, fits the new source's visible bounds, and writes output/SKU_VARIANT-A.png, B, etc. Successful SKU composition needs no new confirmation or model call.

For a different memory/storage configuration of the same model, supply only the new value:

    python scripts/artifact_flow.py sku --product-dir "<PRODUCT_DIRECTORY>" --configuration "12/512 ГБ"

This reuses the locked background, product layer, title, version, features, layout and raster; only the configuration line is redrawn at its locked position and font size. Do not research facts again, remeasure, generate a background, create another master or request another lock. The master must already contain CONFIGURATION_RECT. The version and three facts must still apply to the variant.

To change color and configuration together, add --product "<new color.png>". New SKUs with no --configuration inherit the master's configuration. A different model/name or other changes to displayed facts still require a separate master.

Redo the latest SKU:

    python scripts/artifact_flow.py sku --product-dir "<PRODUCT_DIRECTORY>" --redo

For an explicitly named SKU add --target SKU_VARIANT-A. On redo, omitted --product, --configuration and --generated-background preserve that SKU's current product, configuration and background. Append --configuration "12/512 ГБ" to change only its configuration. Retain source identity when reusing cached layers.
A redo preserves the old final until all checks pass, atomically replaces the same filename and does not consume a sequence label.

## 5. Optional background changes

Only when requested:

    python scripts/scene_prompt.py build --mode MASTER --layout "<PRODUCT_DIRECTORY>/reusable/layout.json"

For a locked SKU use --mode SKU --master "<PRODUCT_DIRECTORY>/reusable/master.json".
For a redo append --redo and, only when the user supplied extra requirements, --additional-prompt "<request>". Additions accumulate by product.

Pass successful stdout verbatim to Image Gen. For a new Meta background, pass assets/meta-clear/background.png as the sole referenced_image_paths input; for an SKU use the verified locked ORIGINAL_MASTER_BACKGROUND.png. Inspect an unseen reference first. Omit num_last_images_to_include. Request an opaque background; never send product art, Logo, text or final composites to the model.

A requested change permits one model call. In functions.exec forward the result with generatedImage(result). Missing/unreadable raster, wrong ratio or SKU size mismatch stops the run; never automatically regenerate or resize it.

The prompt keeps top and bottom quiet regions separate. Never merge them across the product. The local renderer supplies the selling-point panel.

For a master redo, discard the previous candidate while keeping reusable product layers:

    python scripts/artifact_flow.py discard-preview --product-dir "<PRODUCT_DIRECTORY>" --candidate-id a

Then preview with --generated-background "<new background.png>". For SKU use that option on sku --redo. Every SKU background must exactly match the locked master's actual raster; matching only the aspect ratio is insufficient.

## Files and checks

Use only the current Meta data format. Do not accept historical style identifiers or manifests, or migrate them automatically. Create a new master in a new output root when the format is unsupported; preserve existing outputs.

Only confirmed ORIGINAL_MASTER_FINAL.png and SKU_VARIANT-*.png belong in output. Cache layout, overlay, bound background and layers in reusable; keep full-size previews outside output. Do not generate production thumbnails.

Check source identity, glyphs, complete outline, title/feature fit, exactly three facts, version, actual raster and output preservation. The user owns master visual acceptance.

    python -B -m unittest discover -s tests -v
