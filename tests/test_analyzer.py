import unittest
import trimesh
from engine.slicer.analyzer import DesignAnalyzer


class TestDesignAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = DesignAnalyzer(crease_angle_deg=25.0)

    def test_circular_disc_shape_detection(self):
        # Create a flat circular disc/cylinder mesh like a metal medallion/plate
        disc = trimesh.creation.cylinder(radius=40.0, height=8.0)
        res = self.analyzer.analyze(disc, margin_xy=5.0, margin_z_top=2.0)

        # Circularity must be > 0.85
        self.assertGreater(res.circularity, 0.85)
        self.assertEqual(res.detected_shape, "round_plate")
        self.assertIn("Disc", res.shape_description)

        # Recommended cylinder should encompass the disc + margin
        self.assertAlmostEqual(res.recommended_cylinder["diameter"], 90.0, delta=1.0)
        self.assertAlmostEqual(res.recommended_cylinder["thickness"], 10.0, delta=1.0)

    def test_tall_cylinder_shape_detection(self):
        # Create a tall cylinder rod/billet
        rod = trimesh.creation.cylinder(radius=25.0, height=60.0)
        res = self.analyzer.analyze(rod, margin_xy=5.0, margin_z_top=2.0)

        self.assertGreater(res.circularity, 0.85)
        self.assertEqual(res.detected_shape, "cylinder")
        self.assertIn("Round Bar", res.shape_description)

    def test_rectangular_stepped_pyramid_detection(self):
        mesh = trimesh.load("tests/fixtures/stepped_pyramid.stl", file_type="stl")
        res = self.analyzer.analyze(mesh, margin_xy=5.0, margin_z_top=2.0)

        # Square base has circularity ~ 0.785
        self.assertLess(res.circularity, 0.82)
        self.assertEqual(res.detected_shape, "box")
        self.assertIn("Block", res.shape_description)
        self.assertEqual(res.recommended_box["width"], 70.0)
        self.assertEqual(res.recommended_box["length"], 70.0)


if __name__ == "__main__":
    unittest.main()
