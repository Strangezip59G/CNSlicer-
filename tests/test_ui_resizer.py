"""
Unit Tests for Resizable Sidebar Splitter and Collapse/Expand Toggle (Creality Slicer Style).
"""
import unittest
from pathlib import Path


class TestSidebarResizerUI(unittest.TestCase):

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.html = (self.root / "ui" / "index.html").read_text(encoding="utf-8")
        self.css = (self.root / "ui" / "static" / "style.css").read_text(encoding="utf-8")
        self.js = (self.root / "ui" / "static" / "app.js").read_text(encoding="utf-8")

    def test_html_contains_sidebar_resizer_and_toggle_button(self):
        self.assertIn('id="sidebar-resizer"', self.html)
        self.assertIn('id="btn-toggle-sidebar"', self.html)
        self.assertIn('id="resizer-arrow"', self.html)
        self.assertIn('class="sidebar-resizer"', self.html)

    def test_css_contains_resizer_and_collapse_styles(self):
        self.assertIn(".sidebar-resizer", self.css)
        self.assertIn("cursor: col-resize", self.css)
        self.assertIn(".resizer-toggle-btn", self.css)
        self.assertIn(".sidebar-left.sidebar-collapsed", self.css)
        self.assertIn("body.is-resizing", self.css)

    def test_js_contains_resizer_and_collapse_functions(self):
        self.assertIn("setupSidebarResizer", self.js)
        self.assertIn("applySidebarWidth", self.js)
        self.assertIn("setCollapsedState", self.js)
        self.assertIn("cnc_slicer_sidebar_width", self.js)
        self.assertIn("cnc_slicer_sidebar_collapsed", self.js)


if __name__ == "__main__":
    unittest.main()
