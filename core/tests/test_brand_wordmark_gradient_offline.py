"""The startup/header circle retains the approved RH mark's normalized gradient."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "products/app/frontend"
SVG = "{http://www.w3.org/2000/svg}"


def startup_logos():
    yield ET.fromstring((WEB / "login/wordmark.svg").read_text())
    text = (WEB / "login/index.html").read_text()
    start = text.index('<svg class="login-logo"')
    yield ET.fromstring(text[start:text.index("</svg>", start) + 6])


class BrandWordmarkGradient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = ET.fromstring((WEB / "brand.svg").read_text())

    def test_same_gradient_colours_and_order(self):
        expected = [stop.attrib["stop-color"].lower() for stop in self.original.iter(SVG + "stop")]
        for logo in startup_logos():
            actual = [stop.attrib["stop-color"].lower() for stop in logo.iter(SVG + "stop")]
            self.assertEqual(actual, expected)

    def test_same_gradient_direction_after_disc_scaling(self):
        source = next(self.original.iter(SVG + "linearGradient"))
        circle = next(self.original.iter(SVG + "circle"))
        radius = float(circle.attrib["r"])
        for logo in startup_logos():
            gradient = next(logo.iter(SVG + "linearGradient"))
            disc = next(node for node in logo.iter() if node.attrib.get("id", "").endswith("researchhelper-disc"))
            target = next(disc.iter(SVG + "circle"))
            target_radius = float(target.attrib["r"])
            for axis in ("x", "y"):
                for endpoint in ("1", "2"):
                    name = axis + endpoint
                    expected = (float(source.attrib[name]) - float(circle.attrib["c" + axis])) / radius
                    actual = (float(gradient.attrib[name]) - float(target.attrib["c" + axis])) / target_radius
                    self.assertAlmostEqual(actual, expected, places=7)

    def test_controlled_dock_icon_embeds_the_same_mark(self):
        icon = ET.fromstring((WEB / "app-icon.svg").read_text())
        embedded = next(node for node in icon.iter(SVG + "svg") if node is not icon)
        original = [stop.attrib["stop-color"].lower() for stop in self.original.iter(SVG + "stop")]
        actual = [stop.attrib["stop-color"].lower() for stop in embedded.iter(SVG + "stop")]
        self.assertEqual(actual, original)


if __name__ == "__main__":
    unittest.main()
