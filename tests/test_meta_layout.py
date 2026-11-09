"""Layout and artifact regression checks; all writes use disposable fixtures."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from artifact_flow import bind_candidate, create_preview, create_sku
from common import atomic_write, sha256_file, verify_information_assets, verify_master
from compose_image import fit_product_in_box
from measure_text import (FONT_DEFAULT, fit_title,
                          prepare_layout, text_width, configuration_overlay)
from scene_prompt import build_prompt


class MetaLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meta-image-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.product = self.root / "产品.png"
        im = Image.new("RGBA", (200, 280))
        ImageDraw.Draw(im).rounded_rectangle((35, 20, 165, 260), radius=15, fill="#233a4b")
        im.save(self.product)
        self.logo = self.root / "logo.png"
        Image.new("RGBA", (80, 80), "#f00").save(self.logo)
        self.features = self.root / "features.json"
        self.features.write_bytes((ROOT / "references/features-example.json").read_bytes())

    def args(self, **changes):
        values = dict(complete_name="Тест часы", title=None, title_lines=None, title_line_1=None,
                      title_line_2=None, version="Глобальная версия", configuration=None, features=str(self.features),
                      product_reference=str(self.product), logo=str(self.logo), output_root=str(self.root),
                      canvas_height=2048, font_path=str(FONT_DEFAULT), font_weight=600)
        return argparse.Namespace(**(values | changes))

    def quiet(self, call, **values):
        with contextlib.redirect_stdout(io.StringIO()):
            call(argparse.Namespace(**values))

    def test_title_wrap_preserves_name_and_model_number(self):
        for name in ("OnePlus Watch 4", "Xiaomi Smart Band 11 Active",
                     "Фитнес-браслет Xiaomi Smart Band 10", "Планшет OnePlus Pad Lite"):
            lines, font = fit_title(name, FONT_DEFAULT, 2, 600)
            self.assertEqual(" ".join(lines).replace("\u00a0", " "), name)
            self.assertLessEqual(len(lines), 2)
            self.assertGreaterEqual(font.size, 112)
            self.assertTrue(all(text_width(font, s, -.035*font.size) <= 1344 for s in lines))
            self.assertFalse(any(s[0].isdigit() for s in lines))

    def test_long_title_shrinks_and_keeps_text_hierarchy(self):
        name = "Умные часы OnePlus Watch 4 Titanium Special Edition"
        layout = prepare_layout(self.args(title=name))
        self.assertEqual(" ".join(layout["title_lines"]).replace("\u00a0", " "), name)
        self.assertEqual(layout["title_line_count"], 2)
        self.assertLess(layout["title_font_size"], 112)
        title_size = layout["title_font_size"]
        rects = {r["text"]: r for r in layout["text_rects"]}
        version_size = rects[layout["version"]]["h"]*2048/1.2
        self.assertLess(version_size, title_size)
        for feature, lines in zip(layout["features"], layout["feature_label_lines"]):
            headline_size = rects[feature["headline"]]["h"]*2048/1.12
            label_size = rects[lines[0]]["h"]*2048/1.18
            self.assertLess(headline_size, title_size)
            self.assertLess(label_size, headline_size)
        lines, font = fit_title(name, FONT_DEFAULT, 2, 600, [name])
        self.assertEqual(lines, [name])
        self.assertLess(font.size, title_size)
        self.assertLessEqual(text_width(font, name, -.035*font.size), 1344)

    def test_supporting_text_keeps_minimum_sizes_with_long_title(self):
        name = "Фитнес-браслет Xiaomi Smart Band 10 с AMOLED-экраном и 150+ режимами спорта"
        for height in (1024, 1448, 2048):
            with self.subTest(height=height):
                layout = prepare_layout(self.args(title=name, canvas_height=height,
                                                  complete_name=f"Размер {height}"))
                self.assertEqual(" ".join(layout["title_lines"]).replace("\u00a0", " "), name)
                rects = {r["text"]: r for r in layout["text_rects"]}
                sizes = [(rects[layout["version"]]["h"]*height/1.2, 28)]
                for feature, lines in zip(layout["features"], layout["feature_label_lines"]):
                    sizes.append((rects[feature["headline"]]["h"]*height/1.12, 26))
                    sizes.extend((rects[line]["h"]*height/1.18, 22) for line in lines)
                for actual, minimum in sizes:
                    self.assertGreaterEqual(actual+1e-8, minimum*height/1024)
                    self.assertLess(actual, minimum*height/1024+1)
                for r in layout["text_rects"]:
                    self.assertGreaterEqual(min(r["x"], r["y"]), 0)
                    self.assertLessEqual(r["x"]+r["w"], 1)
                    self.assertLessEqual(r["y"]+r["h"], 1)

    def test_long_features_shrink_and_center_each_text_area(self):
        features = json.loads(self.features.read_text(encoding="utf-8"))
        for feature, (headline, label) in zip(features, (
            ("Защита экрана", "и корпуса на 360°"),
            ("Аппаратное снижение", "уровня синего света"),
            ("Мощные", "двойные динамики"),
        )):
            feature.update(headline=headline, label=label)
        self.features.write_text(json.dumps(features), encoding="utf-8")
        for height in (1024, 1448, 2048):
            layout = prepare_layout(self.args(canvas_height=height, complete_name=f"Center {height}"))
            rects = {r["text"]: r for r in layout["text_rects"]}
            self.assertEqual(len(layout["feature_headline_lines"][1]), 2)
            self.assertEqual(len(layout["feature_label_lines"][1]), 2)
            for key, field in (("feature_headline_lines", "headline"), ("feature_label_lines", "label")):
                centers = []
                for i, lines in enumerate(layout[key]):
                    self.assertEqual(" ".join(lines), features[i][field])
                    bounds = [rects[line] for line in lines]
                    centers.append((bounds[0]["y"]+bounds[-1]["y"]+bounds[-1]["h"])/2)
                    for r in bounds:
                        self.assertAlmostEqual(r["x"]+r["w"]/2, (52+(i+.5)*664/3)/768)
                self.assertAlmostEqual(min(centers), max(centers))
            size = rects[layout["feature_headline_lines"][1][0]]["h"]*height/1.12
            self.assertGreaterEqual(size+1e-8, 26*height/1024)
            self.assertLess(size, 26*height/1024+1)
            panel = layout["elements"]["FEATURE_PANEL_RECT"]
            self.assertLessEqual(panel["y"]+panel["h"], 1014/1024)

    def test_visible_product_bounds_cover_wide_tall_and_square(self):
        rect = {"x":64/768,"y":308/1024,"w":640/768,"h":478/1024}
        for w, h in ((100, 400), (600, 120), (200, 200)):
            im = Image.new("RGBA", (w+40, h+40))
            im.paste((23, 70, 120, 255), (20,20,w+20,h+20))
            fitted, x, y = fit_product_in_box(im, rect, 768, 1024)
            self.assertAlmostEqual(fitted.width/fitted.height, w/h, delta=.02)
            self.assertGreaterEqual(x, 64)
            self.assertGreaterEqual(y, 308)
            self.assertLessEqual(x+fitted.width, 704)
            self.assertLessEqual(y+fitted.height, 786)
            self.assertEqual(fitted.getchannel("A").getextrema(), (255,255))

    def test_configuration_follows_title_and_reserves_product_space(self):
        for name, line_count in (("HONOR 600 Lite", 1), ("OnePlus Pad Lite Global Edition", 2)):
            with self.subTest(name=name):
                layout = prepare_layout(self.args(complete_name=name, configuration="8/256 ГБ"))
                self.assertEqual(layout["configuration"], "8/256 ГБ")
                self.assertEqual(layout["title_line_count"], line_count)
                config = layout["elements"]["CONFIGURATION_RECT"]
                product = layout["elements"]["PRODUCT_RECT"]
                title = layout["elements"]["TITLE_LINE_RECT"][-1]
                self.assertAlmostEqual(config["x"]+config["w"]/2, .5)
                self.assertAlmostEqual(config["y"]-title["y"]-title["h"], 12/1024)
                self.assertGreaterEqual(product["y"]-config["y"]-config["h"]+1e-8, 20/1024)
                self.assertGreaterEqual(config["h"]*2048/1.2+1e-8, 64)
                self.assertAlmostEqual(product["y"]+product["h"], 786/1024)
                zone = layout["clear_zones"][0]
                self.assertLessEqual(config["y"]+config["h"], zone["y"]+zone["h"])
                with Image.open(self.product) as image:
                    fitted, _, top = fit_product_in_box(image, product, 1536, 2048)
                self.assertGreaterEqual(top+1, product["y"]*2048)
                self.assertLessEqual(top+fitted.height, 786/1024*2048+1)

    def test_invalid_configuration_does_not_create_layout(self):
        for config in (" ", "8/256\nГБ", "8/256 ГБ "*30):
            with self.subTest(configuration=config):
                with self.assertRaisesRegex(ValueError, "CONFIGURATION_SINGLE_LINE_REQUIRED|TEXT_OVERFLOW"):
                    prepare_layout(self.args(configuration=config))
                self.assertFalse((self.root/"Тест часы").exists())

    def test_features_and_information_regions(self):
        layout = prepare_layout(self.args(title="Фитнес-браслет Xiaomi Smart Band 10"))
        self.assertEqual(layout["style"], "meta")
        self.assertNotIn("CONFIGURATION_RECT", layout["elements"])
        self.assertAlmostEqual(layout["elements"]["PRODUCT_RECT"]["y"], 308/1024)
        self.assertEqual(len(layout["features"]), 3)
        self.assertTrue(all(1 <= len(lines) <= 2 for lines in layout["feature_label_lines"]))
        region = layout["elements"]["PRODUCT_RECT"]
        for r in layout["text_rects"]:
            self.assertGreaterEqual(min(r["x"],r["y"]), 0)
            self.assertLessEqual(r["x"]+r["w"], 1)
            self.assertLessEqual(r["y"]+r["h"], 1)
            self.assertTrue(r["y"]+r["h"] <= region["y"] or r["y"] >= region["y"]+region["h"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            build_prompt(argparse.Namespace(layout=str(Path(layout["product_directory"])/"reusable/layout.json"),mode="MASTER",master=None,redo=False,additional_prompt=None))
        self.assertIn("Astryx", output.getvalue())
        self.assertIn("TOP_INFORMATION", output.getvalue())
        self.assertIn("BOTTOM_FEATURES", output.getvalue())
        with self.assertRaisesRegex(ValueError, "REFUSE_REMEASURE"):
            prepare_layout(self.args())
        reusable = Path(layout["product_directory"])/"reusable"
        with self.assertRaisesRegex(ValueError, "MASTER_CONFIGURATION_MISSING"):
            configuration_overlay(layout, reusable, "12/512 ГБ")
        verify_information_assets(layout, reusable)
        (reusable/"information-overlay.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
            verify_information_assets(layout, reusable)

    def test_bad_features_rejected_before_creating_product(self):
        original = json.loads(self.features.read_text(encoding="utf-8"))
        for features, error in ((original[:2], "EXACTLY_THREE"),
                                ([original[0] | {"icon":"../battery"}, *original[1:]], "ICON_UNAVAILABLE"),
                                ([original[0] | {"source_url":"file:///a"}, *original[1:]], "OFFICIAL_SOURCE")):
            self.features.write_text(json.dumps(features), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, error):
                prepare_layout(self.args())
            self.assertFalse((self.root/"Тест часы").exists())
        original[0]["label"] = "НеразделяемаяОченьДлиннаяХарактеристика"*3
        self.features.write_text(json.dumps(original), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "FEATURE_LABEL_OVERFLOW"):
            prepare_layout(self.args())

    def test_master_sku_reuse_and_failed_redo_preserves_final(self):
        layout = prepare_layout(self.args(configuration="8/256 ГБ"))
        product_dir = Path(layout["product_directory"])
        self.quiet(create_preview, product_dir=str(product_dir), candidate_id="a", product=str(self.product), generated_background=None)
        preview = product_dir/"master-candidate-a-preview.png"
        expected_hash = sha256_file(preview)
        self.quiet(bind_candidate, product_dir=str(product_dir), candidate_id="a")
        master = product_dir/"output/ORIGINAL_MASTER_FINAL.png"
        self.assertEqual(sha256_file(master), expected_hash)
        contract = verify_master(product_dir)["raster"]
        self.assertEqual((contract["width"], contract["height"]), (1086,1448))
        other = self.root/"Другой цвет.png"
        Image.new("RGBA", (330,210), "#8aaac0").save(other)
        sku_args = dict(product_dir=str(product_dir),product=str(other),generated_background=None,configuration=None,redo=False,target=None)
        self.quiet(create_sku, **sku_args)
        final = product_dir/"output/SKU_VARIANT-A.png"
        with Image.open(master) as a, Image.open(final) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()
            self.assertIsNotNone(diff)
            self.assertGreaterEqual(diff[1], round(308/1024*1448))
            self.assertLessEqual(diff[3], round(786/1024*1448)+1)
        final_hash = sha256_file(final)
        wrong = self.root/"wrong.png"
        Image.new("RGB", (96,128), "white").save(wrong)
        with self.assertRaisesRegex(ValueError, "SKU_BACKGROUND_RASTER_MISMATCH"):
            self.quiet(create_sku, **(sku_args | {"redo":True,"generated_background":str(wrong)}))
        self.assertEqual(sha256_file(final), final_hash)
        self.quiet(create_sku, **(sku_args | {"redo":True}))
        self.assertEqual(sha256_file(final), final_hash)
        self.quiet(create_sku, **sku_args)
        self.assertTrue((product_dir/"output/SKU_VARIANT-B.png").exists())
        self.assertEqual(sha256_file(master), expected_hash)

    def test_configuration_sku_reuses_master_and_preserves_redo_state(self):
        layout = prepare_layout(self.args(configuration="8/256 ГБ"))
        product_dir = Path(layout["product_directory"])
        self.quiet(create_preview, product_dir=str(product_dir), candidate_id="a", product=str(self.product), generated_background=None)
        self.quiet(bind_candidate, product_dir=str(product_dir), candidate_id="a")
        master = verify_master(product_dir)
        protected = {entry["path"]:entry["sha256"] for entry in master["files"].values()}
        protected["reusable/information-overlay.png"] = sha256_file(product_dir/"reusable/information-overlay.png")
        self.product.unlink()  # Configuration reuse must work from cached master layers alone.
        args = dict(product_dir=str(product_dir), product=None, generated_background=None,
                    configuration="12/512 ГБ", redo=False, target=None)
        self.quiet(create_sku, **args)
        final = product_dir/"output/SKU_VARIANT-A.png"
        manifest = product_dir/"SKU_VARIANT-A-preview.json"
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["configuration"], "12/512 ГБ")
        config = layout["elements"]["CONFIGURATION_RECT"]

        def only_configuration_changed(a, b):
            with Image.open(a) as left, Image.open(b) as right:
                diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB")).getbbox()
                self.assertIsNotNone(diff)
                self.assertGreaterEqual(diff[1], config["y"]*left.height-6)
                self.assertLessEqual(diff[3], (config["y"]+config["h"])*left.height+6)

        only_configuration_changed(product_dir/"output/ORIGINAL_MASTER_FINAL.png", final)
        final_hash, manifest_hash = sha256_file(final), sha256_file(manifest)
        with patch("artifact_flow.atomic_write", side_effect=OSError("manifest write failed")):
            with self.assertRaisesRegex(OSError, "manifest write failed"):
                self.quiet(create_sku, **(args | {"redo":True,"configuration":"16/512 ГБ"}))
        self.assertEqual(sha256_file(final), final_hash)
        self.assertEqual(sha256_file(manifest), manifest_hash)
        with self.assertRaisesRegex(ValueError, "TEXT_OVERFLOW"):
            self.quiet(create_sku, **(args | {"redo":True,"configuration":"12/512 ГБ "*30}))
        self.assertEqual(sha256_file(final), final_hash)
        self.assertEqual(sha256_file(manifest), manifest_hash)
        self.quiet(create_sku, **(args | {"redo":True,"configuration":None}))
        self.assertEqual(sha256_file(final), final_hash)
        self.quiet(create_sku, **(args | {"redo":True,"configuration":"8/256 ГБ"}))
        self.assertEqual(sha256_file(final), protected["output/ORIGINAL_MASTER_FINAL.png"])

        other = self.root/"other.png"
        Image.new("RGBA", (170,220), "#8aaac0").save(other)
        backdrop = self.root/"backdrop.png"
        Image.new("RGB", (1086,1448), "#dde9ee").save(backdrop)
        self.quiet(create_sku, **(args | {"product":str(other),"generated_background":str(backdrop)}))
        color_sku = product_dir/"output/SKU_VARIANT-B.png"
        previous = self.root/"previous.png"
        previous.write_bytes(color_sku.read_bytes())
        self.quiet(create_sku, **(args | {"redo":True,"target":"SKU_VARIANT-B","configuration":"16/512 ГБ"}))
        only_configuration_changed(previous, color_sku)
        current_hash = sha256_file(color_sku)
        self.quiet(create_sku, **(args | {"redo":True,"target":"SKU_VARIANT-B","configuration":None}))
        self.assertEqual(sha256_file(color_sku), current_hash)
        for path, digest in protected.items():
            self.assertEqual(sha256_file(product_dir/path), digest)
        verify_master(product_dir)

    def test_non_meta_layout_rejected_without_touching_files(self):
        product_dir = self.root/"old-product"
        reusable = product_dir/"reusable"
        reusable.mkdir(parents=True)
        layout_path = reusable/"layout.json"
        atomic_write(layout_path, {"product_directory":str(product_dir), "style":"unsupported"})
        expected = layout_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_LAYOUT_STYLE"):
            self.quiet(create_preview, product_dir=str(product_dir), candidate_id="a",
                       product=str(self.product), generated_background=None)
        self.assertEqual(layout_path.read_bytes(), expected)
        self.assertEqual(set(product_dir.rglob("*")), {reusable, layout_path})

    def test_meta_master_reuse_and_prompt(self):
        layout = prepare_layout(self.args(configuration="8/256 ГБ"))
        product_dir = Path(layout["product_directory"])
        self.quiet(create_preview, product_dir=str(product_dir), candidate_id="a",
                   product=str(self.product), generated_background=None)
        self.quiet(bind_candidate, product_dir=str(product_dir), candidate_id="a")
        reusable = product_dir/"reusable"
        master = verify_master(product_dir)
        self.assertEqual(set(master["files"]), {"layout", "background", "product", "final"})
        master_hash = sha256_file(reusable/"master.json")
        args = dict(product_dir=str(product_dir), product=None, generated_background=None,
                    configuration=None, redo=False, target=None)
        self.quiet(create_sku, **args)
        final = product_dir/"output/SKU_VARIANT-A.png"
        self.assertEqual(sha256_file(final), sha256_file(product_dir/"output/ORIGINAL_MASTER_FINAL.png"))
        self.assertEqual(sha256_file(reusable/"master.json"), master_hash)
        prompt_args = dict(layout=str(reusable/"layout.json"), mode="SKU",
                           master=str(reusable/"master.json"), redo=True, additional_prompt="Keep it light")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            build_prompt(argparse.Namespace(**prompt_args))
        self.assertIn("locked master background", output.getvalue())
        self.assertIn("TOP_INFORMATION", output.getvalue())
        self.assertIn("BOTTOM_FEATURES", output.getvalue())
        self.assertIn("Keep it light", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            build_prompt(argparse.Namespace(**(prompt_args | {"redo":False, "additional_prompt":None})))
        self.assertIn("Keep it light", output.getvalue())

        manifest_path = product_dir/"SKU_VARIANT-A-preview.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        final_hash = sha256_file(final)
        manifest_path.unlink()
        with self.assertRaisesRegex(ValueError, "JSON_UNREADABLE"):
            self.quiet(create_sku, **(args | {"redo":True}))
        self.assertEqual(sha256_file(final), final_hash)
        without_configuration = dict(manifest)
        del without_configuration["configuration"]
        for invalid in (without_configuration, manifest | {"files":manifest["files"] | {"unexpected":{}}}):
            atomic_write(manifest_path, invalid)
            with self.assertRaisesRegex(ValueError, "SKU_MANIFEST_INVALID"):
                self.quiet(create_sku, **(args | {"redo":True}))
            self.assertEqual(sha256_file(final), final_hash)
        master["files"]["unexpected"] = {}
        atomic_write(reusable/"master.json", master)
        with self.assertRaisesRegex(ValueError, "MASTER_ASSET_SET_MISMATCH"):
            verify_master(product_dir)


if __name__ == "__main__":
    unittest.main()
