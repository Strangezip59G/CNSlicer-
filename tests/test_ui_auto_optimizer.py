"""
Unit Tests for Auto-Machining Optimizer UI & Frontend Tab Integration.
"""
import unittest
from pathlib import Path


class TestAutoOptimizerUI(unittest.TestCase):

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.html = (self.root / "ui" / "index.html").read_text(encoding="utf-8")
        self.css = (self.root / "ui" / "static" / "style.css").read_text(encoding="utf-8")
        self.js = (self.root / "ui" / "static" / "app.js").read_text(encoding="utf-8")

    def test_html_contains_studio_tab_bar_and_optimizer_sidebar(self):
        self.assertIn("studio-tab-bar", self.html)
        self.assertIn("tab-btn-manual", self.html)
        self.assertIn("tab-btn-auto-optimizer", self.html)
        self.assertIn("sidebar-manual-cam", self.html)
        self.assertIn("sidebar-auto-optimizer", self.html)

    def test_html_contains_five_profiles_and_four_materials(self):
        profiles = ["fastest", "tool_life", "balanced", "surface_finish", "lowest_energy"]
        for p in profiles:
            self.assertIn(f'data-profile="{p}"', self.html)

        materials = ["wood_mdf", "plastic_delrin", "aluminum_6061", "steel_mild"]
        for m in materials:
            self.assertIn(f'value="{m}"', self.html)

        finishes = ["roughing", "standard", "fine"]
        for f in finishes:
            self.assertIn(f'data-finish="{f}"', self.html)

    def test_html_contains_gcode_arc_options_and_performance_matrix(self):
        self.assertIn("opt-chk-arc-fitting", self.html)
        self.assertIn("opt-chk-micro-filter", self.html)
        self.assertIn("opt-chk-collinear-merge", self.html)
        self.assertIn("opt-chk-corner-decel", self.html)
        self.assertIn("comparison-matrix-grid", self.html)
        self.assertIn("comp-metric-card", self.html)
        self.assertIn("btn-opt-apply", self.html)
        self.assertIn("btn-opt-recalc", self.html)

    def test_css_contains_optimizer_styles(self):
        self.assertIn(".studio-tab-bar", self.css)
        self.assertIn(".studio-tab", self.css)
        self.assertIn(".opt-profile-card", self.css)
        self.assertIn(".comparison-matrix-grid", self.css)
        self.assertIn(".comp-metric-card", self.css)

    def test_js_contains_optimizer_functions(self):
        self.assertIn("setupAutoOptimizerStudio", self.js)
        self.assertIn("switchStudioTab", self.js)
        self.assertIn("runOptimizerAnalysis", self.js)
        self.assertIn("applyOptimizerToolpath", self.js)


if __name__ == "__main__":
    unittest.main()
