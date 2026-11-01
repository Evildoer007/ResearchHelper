"""Independent exact outer-frame acceptance; center artwork remains ResearchHelper."""
import hashlib
import json
import math
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = Path("/Users/haoranxu/Desktop/OptionHelper/assets/icons/optionhelper-app-icon-tile-light.svg")
OUTPUT = ROOT / "result/acceptance/icon-parity"
NS = "{http://www.w3.org/2000/svg}"


def structure(element):
    return element.tag, sorted(element.attrib.items()), (element.text or "").strip(), [structure(child) for child in element]


def outside_circle_difference(first, second, center_antialias=2):
    first, second = first.convert("RGBA"), second.convert("RGBA")
    if first.size != second.size:
        raise ValueError("Icon sizes differ")
    size = first.width
    radius = size * 303.6 / 1024 + center_antialias
    center = size / 2
    changed = compared = 0
    a, b = first.load(), second.load()
    for y in range(size):
        for x in range(size):
            if math.hypot(x + .5 - center, y + .5 - center) > radius:
                compared += 1
                changed += a[x, y] != b[x, y]
    return {"size": size, "outer_pixels_compared": compared, "outer_pixels_changed": changed}


class IconOuterParityTests(unittest.TestCase):
    def test_outer_shaders_and_all_three_rectangles_equal_reference(self):
        research = ET.fromstring((ROOT / "products/app/frontend/app-icon.svg").read_text())
        reference = ET.fromstring(REFERENCE.read_text())
        for identifier in ("tile", "tileSheen", "tileShadow"):
            with self.subTest(component=identifier):
                self.assertEqual(structure(research.find(f'.//*[@id="{identifier}"]')),
                                 structure(reference.find(f'.//*[@id="{identifier}"]')))
        self.assertEqual([structure(rect) for rect in research.findall(NS + "rect")],
                         [structure(rect) for rect in reference.findall(NS + "rect")])

    def test_center_is_exact_approved_demo_mark_and_matches_reference_size(self):
        text = (ROOT / "products/app/frontend/app-icon.svg").read_text()
        brand = (ROOT / "products/app/frontend/brand.svg").read_text()
        marker = 'viewBox="0 0 40 40">'
        self.assertEqual(text.split(marker, 1)[1].split('</svg>', 1)[0],
                         brand.split(marker, 1)[1].split('</svg>', 1)[0])
        center = ET.fromstring(text).find(NS + "svg")
        self.assertAlmostEqual(float(center.get("x")) + float(center.get("width"))/2, 512)
        self.assertAlmostEqual(float(center.get("y")) + float(center.get("height"))/2, 512)
        self.assertAlmostEqual(float(center.get("width")) * 19/40, 303.6)

    def test_all_nine_rendered_outer_frames_are_pixel_identical(self):
        render = json.loads((OUTPUT / "render.json").read_text())
        self.assertTrue(render["headless"])
        self.assertFalse(render["external_network"])
        for name, file in render["source_paths"].items():
            self.assertEqual(hashlib.sha256(Path(file).read_bytes()).hexdigest(), render["source_hashes"][name])
        checks = []
        for size in render["sizes"]:
            with self.subTest(size=size), Image.open(OUTPUT / f"ResearchHelper-{size}.png") as first, \
                    Image.open(OUTPUT / f"OptionHelper-{size}.png") as second:
                difference = outside_circle_difference(first, second)
                self.assertEqual(difference["outer_pixels_changed"], 0, difference)
                checks.append(difference)
        (OUTPUT / "parity.json").write_text(json.dumps({"source_hashes": render["source_hashes"], "checks": checks}, indent=2))


if __name__ == "__main__":
    unittest.main()
