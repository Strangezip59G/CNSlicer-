from __future__ import annotations
import unittest
from pathlib import Path


class TestUIVectorCAM(unittest.TestCase):
    """UI Verification Test Suite for 2D Vector CAM (Beta) integration."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        with open(cls.root / "ui" / "index.html", "r", encoding="utf-8") as f:
            cls.html = f.read()
        with open(cls.root / "ui" / "static" / "app.js", "r", encoding="utf-8") as f:
            cls.js = f.read()
        with open(cls.root / "ui" / "static" / "style.css", "r", encoding="utf-8") as f:
            cls.css = f.read()

    def test_html_contains_vector_cam_studio_elements(self):
        # 1. Mode selector tab
        self.assertIn('id="tab-btn-vector-cam"', self.html)
        self.assertIn('Vector CAM', self.html)
        self.assertIn('class="badge-beta-pill"', self.html)

        # 2. Vector CAM Inspector
        self.assertIn('id="sidebar-vector-cam"', self.html)
        self.assertIn('id="vector-file-input"', self.html)
        self.assertIn('id="vector-drop-zone"', self.html)
        self.assertIn('id="btn-sample-logo"', self.html)
        self.assertIn('id="btn-sample-gear"', self.html)
        self.assertIn('id="btn-sample-sketch"', self.html)

        # 3. Holding tabs & operation pills
        self.assertIn('id="vector-op-selector"', self.html)
        self.assertIn('data-op="profile_outside"', self.html)
        self.assertIn('data-op="profile_inside"', self.html)
        self.assertIn('data-op="pocket"', self.html)
        self.assertIn('data-op="engrave"', self.html)
        self.assertIn('id="chk-vector-tabs"', self.html)
        self.assertIn('id="btn-vector-slice"', self.html)

    def test_js_contains_vector_cam_functions(self):
        self.assertIn("setupVectorCAMStudio", self.js)
        self.assertIn("uploadVectorFile", self.js)
        self.assertIn("loadVectorSample", self.js)
        self.assertIn("runImageRetrace", self.js)
        self.assertIn("renderVectorGeometry", self.js)
        self.assertIn("runVectorSlicing", self.js)
        self.assertIn("vectorLinesGroup", self.js)

    def test_css_contains_vector_cam_styles(self):
        self.assertIn(".badge-beta-pill", self.css)
        self.assertIn(".vector-hero", self.css)
        self.assertIn(".vector-drop-area", self.css)
        self.assertIn(".vector-op-pills", self.css)
        self.assertIn(".btn-op-pill", self.css)


if __name__ == "__main__":
    unittest.main()
