"""Bundled icons must retain their official source and usable alpha masks."""
import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image


class LucideAssetTests(unittest.TestCase):
    def test_source_and_raster_pairs(self):
        folder = Path(__file__).resolve().parents[1] / "assets" / "lucide"
        sources = json.loads((folder / "SOURCES.json").read_text(encoding="utf-8"))
        names = {item["name"] for item in sources}
        self.assertEqual(len(names), len(sources))
        for extension in ("svg", "png"):
            self.assertEqual(names, {p.stem for p in folder.glob(f"*.{extension}")})
        for item in sources:
            with self.subTest(icon=item["name"]):
                svg = (folder / (item["name"] + ".svg")).read_bytes()
                # Existing checkout SVGs may have Windows line endings.
                svg = svg.replace(b"\r\n", b"\n")
                blob = b"blob " + str(len(svg)).encode() + b"\0" + svg
                self.assertEqual(hashlib.sha1(blob).hexdigest(), item["sha"])
                with Image.open(folder / (item["name"] + ".png")) as image:
                    self.assertEqual(image.size, (256, 256))
                    self.assertEqual(image.mode, "RGBA")
                    alpha = image.getchannel("A")
                    self.assertEqual(alpha.getextrema(), (0, 255))
                    bounds = alpha.getbbox()
                    self.assertIsNotNone(bounds)
                    self.assertTrue(0 < bounds[0] < bounds[2] < 256)
                    self.assertTrue(0 < bounds[1] < bounds[3] < 256)
