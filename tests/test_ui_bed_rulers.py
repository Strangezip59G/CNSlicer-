import os
import unittest
from engine.slicer.machine import STANDARD_CNC_MACHINES, get_machine_profile

class TestBedPositioningRulers(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.html_path = os.path.join(self.base_dir, "ui", "index.html")
        self.js_path = os.path.join(self.base_dir, "ui", "static", "app.js")
        self.css_path = os.path.join(self.base_dir, "ui", "static", "style.css")

    def test_html_contains_bed_numbers_and_mounting_card(self):
        with open(self.html_path, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="chk-show-bed-numbers"', html)
        self.assertIn('id="stock-clamping-card"', html)
        self.assertIn('id="clamp-span-center"', html)
        self.assertIn('id="clamp-span-corner"', html)

    def test_js_contains_ruler_and_clamping_functions(self):
        with open(self.js_path, "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("createBedPositioningRulers", js)
        self.assertIn("createRulerTextureX", js)
        self.assertIn("createRulerTextureY", js)
        self.assertIn("createCenterOriginTarget", js)
        self.assertIn("createCornerHomeMarker", js)
        self.assertIn("updateStockClampingCard", js)
        self.assertIn("bedNumbersMesh", js)

    def test_css_contains_mounting_card_styles(self):
        with open(self.css_path, "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".mounting-guide-card", css)
        self.assertIn(".coord-badge", css)

    def test_machine_profiles_have_valid_bed_dimensions(self):
        for machine_id, m in STANDARD_CNC_MACHINES.items():
            self.assertGreater(m.bed_width_x, 50)
            self.assertGreater(m.bed_length_y, 50)
            self.assertGreater(m.max_z_travel, 10)

if __name__ == "__main__":
    unittest.main()
