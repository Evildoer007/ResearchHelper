"""All active wordmarks retain the latest supplied paths, paint and proportions."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "products/app/frontend"
SVG = "{http://www.w3.org/2000/svg}"


def normalized(element):
    ignored = {"class", "role", "aria-label", "aria-labelledby"}
    def clean(value):
        return value.replace("header-", "").replace("fallback-", "")
    return (element.tag, sorted((key, clean(value)) for key, value in element.attrib.items()
                               if key not in ignored), (element.text or "").strip(),
            [normalized(child) for child in element])


class BrandWordmarkGradient(unittest.TestCase):
    def test_runtime_identity_copies_exact_canonical_bytes(self):
        for canonical, runtime in (("researchhelper-logo.svg", "login/wordmark.svg"),
                                   ("researchhelper-mark.svg", "brand.svg"),
                                   ("researchhelper-app-icon-tile-light.svg", "app-icon.svg"),
                                   ("researchhelper-app-icon-tile-dark.svg", "app-icon-dark.svg")):
            with self.subTest(runtime=runtime):
                self.assertEqual((ROOT / "assets/icons" / canonical).read_bytes(),
                                 (WEB / runtime).read_bytes())

    def test_inline_header_and_fallback_preserve_supplied_artwork(self):
        expected = normalized(ET.fromstring((WEB / "login/wordmark.svg").read_text()))
        html = (WEB / "login/index.html").read_text()
        for name in ("login-logo", "login-splash__fallback-logo"):
            with self.subTest(surface=name):
                start = html.index('<svg class="' + name + '"')
                logo = ET.fromstring(html[start:html.index("</svg>", start) + 6])
                self.assertEqual(normalized(logo), expected)
        identifiers = re.findall(r'\bid="([^\"]+)"', html)
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_animation_targets_the_supplied_axis_and_two_endpoints(self):
        script = (WEB / "login/login-startup.js").read_text()
        self.assertIn('["payoff", "dot-start", "dot-end", "axis"]', script)
        self.assertNotIn('querySelector("#researchhelper-arc")', script)
        self.assertNotIn('dot-3', script)
        self.assertIn('Incomplete packaged logo', script)


if __name__ == "__main__":
    unittest.main()
