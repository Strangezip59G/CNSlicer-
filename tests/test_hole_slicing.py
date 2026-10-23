import unittest
import numpy as np
import shapely.geometry as sg
import trimesh

from engine.slicer.analyzer import DesignAnalyzer
from engine.slicer.auto_pilot import AutoPilot
from engine.slicer.machine import STANDARD_CNC_MACHINES
from engine.slicer.stock import StockBlock
from engine.slicer.subtractive import SubtractiveSlicer, reconstruct_planar_polygons
from engine.slicer.tool import CuttingTool, ToolType


class TestHoleSlicingAndAnalysis(unittest.TestCase):
    """Test suite verifying robust detection, rendering, and subtractive toolpath generation
    for 3D models with holes, bores, and cavities in the main body from the top face.
    """

    def setUp(self):
        # Create a rectangular block (100 x 100 x 30 mm) with:
        # 1. Blind hole (radius 8 mm, depth 15 mm) at (25, 25)
        # 2. Through hole (radius 6 mm, depth 30 mm) at (-25, -25)
        self.box = trimesh.creation.box(extents=[100.0, 100.0, 30.0])
        
        # Blind hole from top (Z=15 down to Z=0 in local coords)
        self.blind_cyl = trimesh.creation.cylinder(radius=8.0, height=20.0).apply_translation([25.0, 25.0, 7.5])
        
        # Through hole (Z=15 to Z=-15)
        self.through_cyl = trimesh.creation.cylinder(radius=6.0, height=40.0).apply_translation([-25.0, -25.0, 0.0])
        
        self.part = self.box.difference([self.blind_cyl, self.through_cyl])
        # Translate so part bottom sits at Z = 0 and top sits at Z = 30
        self.part.apply_translation([0.0, 0.0, 15.0])

        self.stock = StockBlock(width_x=120.0, length_y=120.0, thickness_z=30.0, spoilboard_z=0.0)
        self.tool = CuttingTool(
            tool_id="flat_6mm",
            name="6mm Endmill",
            tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=6.0,
            stepdown_mm=5.0
        )
        self.slicer = SubtractiveSlicer(tool=self.tool, stock=self.stock)

    def test_reconstruct_planar_polygons_preserves_interior_holes(self):
        """Verify that nested loops are not erased by union, and correctly form holes."""
        outer_box = sg.box(-50, -50, 50, 50)
        hole1 = sg.Point(25, 25).buffer(8.0)
        hole2 = sg.Point(-25, -25).buffer(6.0)

        # Pass raw solid polygons
        result = reconstruct_planar_polygons([outer_box, hole1, hole2])

        self.assertTrue(isinstance(result, sg.Polygon))
        self.assertEqual(len(result.interiors), 2)
        # Expected area: 100*100 - pi*8^2 - pi*6^2 ~ 10000 - 201.06 - 113.10 = ~9685.84
        self.assertAlmostEqual(result.area, 10000.0 - np.pi * (8.0**2 + 6.0**2), delta=5.0)

    def test_design_analyzer_detects_top_face_holes(self):
        """Verify that DesignAnalyzer detects top-face holes, calculates diameters, and exposes them in silhouette_interiors."""
        analyzer = DesignAnalyzer(crease_angle_deg=25.0)
        analysis = analyzer.analyze(self.part, margin_xy=5.0, margin_z_top=0.0)

        self.assertTrue(analysis.has_top_holes)
        self.assertGreaterEqual(len(analysis.detected_holes), 2)
        
        # Check detected diameters
        diameters = [h["diameter_mm"] for h in analysis.detected_holes]
        self.assertTrue(any(abs(d - 16.0) < 1.0 for d in diameters))  # 8mm radius -> 16mm dia
        self.assertTrue(any(abs(d - 12.0) < 1.0 for d in diameters))  # 6mm radius -> 12mm dia
        
        self.assertAlmostEqual(analysis.min_hole_diameter_mm, 12.0, delta=1.0)
        # Silhouette interiors must contain the hole rings for 3D viewport bed projection
        self.assertGreaterEqual(len(analysis.silhouette_interiors), 2)

    def test_slicer_generates_pocket_clearing_inside_top_holes(self):
        """Verify that subtractive slicing produces cutting moves inside the internal holes."""
        result = self.slicer.slice_3d_mesh(self.part)

        self.assertGreater(len(result.layers), 0)

        # Check layers where both holes are active (e.g. Z = 25.0)
        layer_top = result.layers[0]
        self.assertAlmostEqual(layer_top.nominal_z, 25.0, delta=0.5)

        # Points inside the blind hole around (25, 25)
        blind_hole_points = [
            p for seg in layer_top.segments for p in seg.points
            if (p.x - 25.0)**2 + (p.y - 25.0)**2 < 6.0**2
        ]
        self.assertGreater(len(blind_hole_points), 0, "Toolpath must enter and pocket the blind hole at (25, 25)!")

        # Points inside the through hole around (-25, -25)
        through_hole_points = [
            p for seg in layer_top.segments for p in seg.points
            if (p.x + 25.0)**2 + (p.y + 25.0)**2 < 5.0**2
        ]
        self.assertGreater(len(through_hole_points), 0, "Toolpath must enter and pocket the through hole at (-25, -25)!")

    def test_slicer_generates_profile_contour_around_hole_walls(self):
        """Verify that inner hole walls receive dedicated profile finishing passes."""
        result = self.slicer.slice_3d_mesh(self.part)

        layer_top = result.layers[0]
        # Profile segments near the blind hole wall (radius 8mm - tool_r 3mm = 5mm from (25,25))
        blind_profile_pts = [
            p for seg in layer_top.segments if seg.operation.value == "profile"
            for p in seg.points if (p.x - 25.0)**2 + (p.y - 25.0)**2 < 7.0**2
        ]
        self.assertGreater(len(blind_profile_pts), 0, "Profile finish pass must contour inner blind hole perimeter!")

    def test_auto_pilot_downsizes_cutter_for_small_holes(self):
        """Verify Auto-Pilot downsizes tool diameter if the recommended cutter is larger than internal holes."""
        analyzer = DesignAnalyzer()
        analysis = analyzer.analyze(self.part)
        machine = STANDARD_CNC_MACHINES["haas_vf2"]

        rec = AutoPilot.compute_recommendations(analysis, machine)
        # Even on heavy VMC (which would otherwise pick 6mm or 6.35mm cutter),
        # if minimum hole is detected, tool diameter must be <= min_hole_diameter
        self.assertLessEqual(rec.tool_diameter_mm, analysis.min_hole_diameter_mm)


if __name__ == "__main__":
    unittest.main()
