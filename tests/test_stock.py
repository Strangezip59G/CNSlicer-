import unittest
import numpy as np
import shapely.geometry as sg
import trimesh

from engine.slicer.stock import StockBlock, StockShape
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.slicer.analyzer import DesignAnalyzer


class TestStockShapesAndAnalysis(unittest.TestCase):

    def setUp(self):
        self.tool = CuttingTool(
            tool_id=1,
            name='1/8" Endmill',
            tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175,
            stepdown_mm=2.0,
            stepover_fraction=0.45,
            feedrate_xy_mm_min=1200.0,
            plunge_rate_z_mm_min=300.0
        )

    def test_box_and_plate_stock(self):
        stock = StockBlock(
            shape=StockShape.BOX,
            width_x=80.0,
            length_y=60.0,
            thickness_z=25.0,
            origin_x=10.0,
            origin_y=15.0
        )
        self.assertAlmostEqual(stock.center_x, 50.0)
        self.assertAlmostEqual(stock.center_y, 45.0)
        self.assertAlmostEqual(stock.polygon.area, 80.0 * 60.0)
        self.assertTrue(stock.contains_xy(50.0, 45.0))
        self.assertFalse(stock.contains_xy(0.0, 0.0))

        # Test set_center repositions correctly
        stock.set_center(100.0, 100.0)
        self.assertAlmostEqual(stock.center_x, 100.0)
        self.assertAlmostEqual(stock.center_y, 100.0)
        self.assertAlmostEqual(stock.origin_x, 60.0)
        self.assertAlmostEqual(stock.origin_y, 70.0)

    def test_cylinder_and_round_plate_stock(self):
        stock = StockBlock(
            shape=StockShape.CYLINDER,
            diameter_d=80.0,
            thickness_z=30.0,
            origin_x=10.0,
            origin_y=10.0
        )
        self.assertEqual(stock.radius_r, 40.0)
        self.assertAlmostEqual(stock.center_x, 50.0)
        self.assertAlmostEqual(stock.center_y, 50.0)

        # Polygon must be circular with area approx pi * r^2
        expected_area = np.pi * (40.0**2)
        self.assertAlmostEqual(stock.polygon.area, expected_area, delta=15.0)
        self.assertTrue(stock.contains_xy(50.0, 50.0))
        self.assertFalse(stock.contains_xy(10.0, 10.0))  # Corner outside circle

        # Test set_center
        stock.set_center(120.0, 150.0)
        self.assertAlmostEqual(stock.center_x, 120.0)
        self.assertAlmostEqual(stock.center_y, 150.0)
        self.assertTrue(stock.contains_xy(120.0, 150.0))

    def test_subtractive_slicing_with_cylinder_stock(self):
        # Create cylinder stock centered at (50, 50) with 80mm diameter
        stock = StockBlock(
            shape=StockShape.CYLINDER,
            diameter_d=80.0,
            thickness_z=20.0,
            origin_x=10.0,
            origin_y=10.0
        )
        slicer = SubtractiveSlicer(stock=stock, tool=self.tool)

        # 30x30x10 rectangular part centered at (50, 50)
        b = trimesh.creation.box(extents=[30, 30, 10])
        b.apply_translation([50, 50, 5])

        result = slicer.slice_3d_mesh(mesh=b, z_bottom=0.0, custom_stepdown=3.0, clearing_pattern="concentric")
        self.assertGreater(result.layer_count, 0)
        self.assertGreater(result.total_cut_length_mm, 50.0)

        # Also test with zigzag clearing on cylinder stock
        result_zigzag = slicer.slice_3d_mesh(mesh=b, z_bottom=0.0, custom_stepdown=3.0, clearing_pattern="zigzag")
        self.assertGreater(result_zigzag.layer_count, 0)
        self.assertGreater(result_zigzag.total_cut_length_mm, 50.0)

    def test_design_analyzer(self):
        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        mesh = trimesh.load("tests/fixtures/stepped_pyramid.stl", file_type="stl")

        analysis = analyzer.analyze(mesh, margin_xy=5.0, margin_z_top=2.0)
        self.assertAlmostEqual(analysis.width_x, 60.0, delta=1.0)
        self.assertAlmostEqual(analysis.length_y, 60.0, delta=1.0)
        self.assertAlmostEqual(analysis.height_z, 30.0, delta=1.0)

        # Recommended stock should include margins (60 + 10 = 70)
        self.assertEqual(analysis.recommended_box["width"], 70.0)
        self.assertEqual(analysis.recommended_box["length"], 70.0)
        self.assertEqual(analysis.recommended_box["thickness"], 32.0)

        # Silhouette exterior points should exist
        self.assertGreater(len(analysis.silhouette_exterior), 3)

        # Feature edges (sharp corners/steps)
        self.assertGreater(len(analysis.feature_edges), 10)


if __name__ == "__main__":
    unittest.main()
