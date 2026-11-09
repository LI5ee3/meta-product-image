# Lucide assets

Official SVG sources and their repository hashes are recorded in SOURCES.json.
LICENSE contains the applicable ISC/MIT notices.

The bundled 256×256 transparent PNGs were rasterized from these SVGs with Chromium,
using a black stroke at 1.8/24 width to match the approved A design. Runtime code
uses their alpha masks and applies the shared icon color. No SVG renderer or
network dependency is required when creating an image.

Available names: activity, battery, camera, monitor, volume-2, watch.
Add any additional icon from the official Lucide source with the same SVG/PNG
pair and license; do not replace it with an emoji or an improvised drawing.
