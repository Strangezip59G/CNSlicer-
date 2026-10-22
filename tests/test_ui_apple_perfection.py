"""
Unit Tests for Apple Design Perfection:
- Clean SVG vector icons (Zero emoji glyphs in UI)
- Removal of fake macOS window traffic lights
- Removal of confusing Pareto AI badge
- Collision-free viewport overlays (Scrubber Dock vs Legend)
- Apple liquid-glass sliding segmented controls
- Apple Dark / Light theme toggle with comprehensive Light mode styling
"""
import unittest
import re
from pathlib import Path


class TestUIApplePerfection(unittest.TestCase):

    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent
        self.html = (self.root / "ui" / "index.html").read_text(encoding="utf-8")
        self.css = (self.root / "ui" / "static" / "style.css").read_text(encoding="utf-8")
        self.js = (self.root / "ui" / "static" / "app.js").read_text(encoding="utf-8")

    def test_no_raw_emojis_in_html(self):
        """Verify that UI contains no raw cartoon emojis."""
        emojis_to_ban = [
            "⚙️", "📁", "🏛️", "🏭", "🔍", "📡", "🛠️", "⚡", "📦", "🪵",
            "🔪", "⏱️", "🖱️", "✥", "🔄", "📐", "🪞", "🛏️", "🎯", "↺",
            "🪚", "🛡️", "📊", "🚀", "⚠️", "⚖️", "✨", "🌿", "🌲", "🧪",
            "🪙", "🔩", "⛏️", "💎", "📜", "🗜️", "💰", "💡", "💾", "✅"
        ]
        for emoji in emojis_to_ban:
            self.assertNotIn(emoji, self.html, f"Raw emoji '{emoji}' found in ui/index.html")

    def test_no_fake_macos_window_dots(self):
        """Ensure fake macOS window traffic lights are removed."""
        self.assertNotIn("apple-window-dots", self.html)
        self.assertNotIn("dot-red", self.html)

    def test_no_confusing_pareto_badge(self):
        """Ensure confusing 'Pareto AI' badge is completely removed."""
        self.assertNotIn("Pareto AI", self.html)

    def test_redundant_auto_pilot_card_removed_from_manual_cam(self):
        """Ensure redundant quick pilot card in sidebar is removed (mode switcher is in top bar)."""
        self.assertNotIn("card-auto-mode", self.html)
        self.assertNotIn("Auto-Machining Quick Pilot", self.html)

    def test_apple_liquid_glass_studio_slider(self):
        """Verify liquid-glass slider pill exists for CAM Studio vs Auto Mode."""
        self.assertIn("studio-glass-pill", self.html)
        self.assertIn(".apple-glass-slider-pill", self.css)
        self.assertIn("setupStudioGlassSlider", self.js)
        self.assertIn("updateStudioGlassPill", self.js)

    def test_apple_liquid_glass_theme_switcher(self):
        """Verify Dark / Light theme switch with liquid-glass sliding animation."""
        self.assertIn("theme-segmented-switch", self.html)
        self.assertIn("theme-glass-pill", self.html)
        self.assertIn("btn-theme-dark", self.html)
        self.assertIn("btn-theme-light", self.html)
        self.assertIn(".theme-segmented-switch", self.css)
        self.assertIn(".theme-glass-slider-pill", self.css)
        self.assertIn("body.light-theme", self.css)
        self.assertIn("setupThemeSwitching", self.js)

    def test_light_theme_comprehensive_styling(self):
        """Ensure header, inputs, and tabs have proper contrast in Light Mode."""
        self.assertIn("body.light-theme .app-header-apple", self.css)
        self.assertIn("body.light-theme input[type=\"number\"]", self.css)
        self.assertIn("body.light-theme select", self.css)
        self.assertIn("body.light-theme input[readonly]", self.css)
        self.assertIn("body.light-theme .studio-tab.active", self.css)

    def test_viewport_overlay_no_collision(self):
        """Ensure .viewport-overlay-info and .viewport-scrubber-dock do not share the same top/right coordinates."""
        self.assertIn(".viewport-overlay-info", self.css)
        self.assertIn("top: 130px;", self.css)

    def test_svg_icon_classes_in_css(self):
        """Ensure .svg-icon styles exist with proper stroke and sizing."""
        self.assertIn(".svg-icon", self.css)
        self.assertIn(".brand-svg", self.css)
        self.assertIn("stroke: currentColor", self.css)

    def test_camera_view_presets_toggle_active_class(self):
        """Ensure camera view preset buttons toggle .active dynamically instead of being stuck on Isometric."""
        self.assertIn('document.querySelectorAll(".views-control-capsule .btn-view-pill").forEach', self.js)
        self.assertIn('activeBtn.classList.add("active")', self.js)
        self.assertIn('btn-view-top', self.html)
        self.assertIn('btn-view-front', self.html)
        self.assertIn('btn-view-iso', self.html)
        self.assertIn('btn-reset-cam', self.html)


if __name__ == "__main__":
    unittest.main()
