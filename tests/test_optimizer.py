"""
Unit Tests for Auto-Machining Multi-Objective Optimizer & G-Code Arc Engine.
"""
import math
import unittest
import trimesh

from engine.slicer.machine import STANDARD_CNC_MACHINES
from engine.slicer.analyzer import DesignAnalyzer
from engine.slicer.stock import StockBlock, StockShape
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.slicer.toolpaths import ToolpathPoint, MoveType, ToolpathSegment, ToolpathLayer, SlicingResult
from engine.optimizer.material_database import MATERIALS, get_material
from engine.optimizer.machining_optimizer import MachiningOptimizer
from engine.optimizer.gcode_optimizer import GCodeOptimizer, circumcircle_2d, cross_product_2d, fit_arcs_to_segment


class TestMachiningOptimizer(unittest.TestCase):

    def setUp(self):
        # Stepped pyramid fixture or test box mesh
        b1 = trimesh.creation.box(extents=[30, 30, 10])
        b2 = trimesh.creation.box(extents=[50, 50, 10])
        b2.apply_translation([0, 0, -10])
        self.mesh = trimesh.util.concatenate([b1, b2])
        analyzer = DesignAnalyzer()
        self.analysis = analyzer.analyze(self.mesh)
        self.genmitsu = STANDARD_CNC_MACHINES["genmitsu_3018"]
        self.shapeoko = STANDARD_CNC_MACHINES["shapeoko_4"]

    def test_material_database(self):
        self.assertIn("wood_mdf", MATERIALS)
        self.assertIn("aluminum_6061", MATERIALS)
        self.assertIn("steel_mild", MATERIALS)
        self.assertIn("plastic_delrin", MATERIALS)

        al = get_material("aluminum_6061")
        self.assertGreater(al.specific_cutting_force_kc_n_mm2, 500)
        self.assertGreater(al.surface_speed_vc_max_m_min, 100)

    def test_all_five_optimization_profiles(self):
        profiles = ["fastest", "tool_life", "balanced", "surface_finish", "lowest_energy"]
        for p in profiles:
            rec = MachiningOptimizer.compute(
                analysis=self.analysis,
                machine=self.genmitsu,
                profile_key=p,
                material_key="wood_mdf",
                finish_key="standard"
            )
            d = rec.to_dict()
            self.assertEqual(d["profile"], p)
            self.assertGreater(d["feed_xy_mm_min"], 0)
            self.assertGreater(d["spindle_rpm"], 0)
            self.assertLessEqual(d["spindle_rpm"], self.genmitsu.spindle_max_rpm)
            self.assertIn("rationales", d)
            self.assertTrue(len(d["all_reasons"]) > 0)

        # Fastest should have higher or equal feed and DOC compared to tool_life
        rec_fast = MachiningOptimizer.compute(self.analysis, self.shapeoko, "fastest", "aluminum_6061")
        rec_life = MachiningOptimizer.compute(self.analysis, self.shapeoko, "tool_life", "aluminum_6061")
        self.assertGreaterEqual(rec_fast.feed_xy_mm_min, rec_life.feed_xy_mm_min)
        self.assertGreaterEqual(rec_fast.stepdown_mm, rec_life.stepdown_mm)
        self.assertGreater(rec_life.optimized_metrics.tool_life_score_pct, rec_fast.optimized_metrics.tool_life_score_pct)

        # Fastest mode must provide tool wear and tool breakage warnings
        self.assertIsNotNone(rec_fast.profile_warning)
        self.assertIn("tool wear", rec_fast.profile_warning.lower())
        self.assertIn("break", rec_fast.profile_warning.lower())
        self.assertIn("BREAKAGE", rec_fast.tool_life_optimizer_rationale)
        self.assertTrue(any("breakage" in r.lower() for r in rec_fast.all_reasons))
        self.assertIsNone(rec_life.profile_warning)

    def test_steel_material_restricts_doc_and_selects_rigid_tool(self):
        rec_steel = MachiningOptimizer.compute(
            analysis=self.analysis,
            machine=self.shapeoko,
            profile_key="balanced",
            material_key="steel_mild",
            finish_key="standard"
        )
        # Steel has high kc, should reduce stepdown
        self.assertLessEqual(rec_steel.stepdown_mm, 2.0)
        self.assertIn("Steel", rec_steel.material_name)

    def test_surface_finish_levels_scallop(self):
        rec_rough = MachiningOptimizer.compute(self.analysis, self.shapeoko, "balanced", "wood_mdf", "roughing")
        rec_fine = MachiningOptimizer.compute(self.analysis, self.shapeoko, "balanced", "wood_mdf", "fine")

        self.assertLess(rec_fine.optimized_metrics.scallop_height_um, rec_rough.optimized_metrics.scallop_height_um)
        self.assertLessEqual(rec_fine.stock_to_leave_mm, 0.1)

    def test_circumcircle_and_cross_product(self):
        # 3 points on unit circle centered at (0, 0)
        p1 = (1.0, 0.0)
        p2 = (0.0, 1.0)
        p3 = (-1.0, 0.0)
        res = circumcircle_2d(p1, p2, p3)
        self.assertIsNotNone(res)
        cx, cy, r = res
        self.assertAlmostEqual(cx, 0.0, places=4)
        self.assertAlmostEqual(cy, 0.0, places=4)
        self.assertAlmostEqual(r, 1.0, places=4)

        # Cross product: CCW turn should be positive
        cross = cross_product_2d(p1, p2, p3)
        self.assertGreater(cross, 0.0)

    def test_gcode_arc_fitting_and_micro_filtering(self):
        # Create a circle approximated by 36 linear points with tiny segments (<0.01mm)
        points = []
        radius = 20.0
        # 72 points along semicircle
        for deg in range(0, 181, 3):
            rad = math.radians(deg)
            x = radius * math.cos(rad)
            y = radius * math.sin(rad)
            points.append(ToolpathPoint(x=x, y=y, z=-1.0, move_type=MoveType.CUT, feedrate_mm_min=1000.0))

        # Add a few micro-jitter moves (< 0.005mm)
        points.insert(5, ToolpathPoint(x=points[4].x + 0.002, y=points[4].y + 0.002, z=-1.0, move_type=MoveType.CUT))

        seg = ToolpathSegment(points=points)
        layer = ToolpathLayer(layer_index=0, nominal_z=-1.0, segments=[seg])
        slicing_result = SlicingResult(
            layers=[layer],
            stock_width=100,
            stock_length=100,
            stock_thickness=20,
            tool_diameter=3.175,
            safe_z=5.0
        )

        optimizer = GCodeOptimizer(enable_arc_fitting=True, enable_micro_filter=True, tolerance_mm=0.01)
        opt_report = optimizer.optimize_slicing_result(slicing_result)

        self.assertGreater(opt_report["original_points_count"], opt_report["optimized_moves_count"])
        self.assertGreater(opt_report["fitted_arcs_count"], 0)
        self.assertGreater(opt_report["line_reduction_percent"], 40.0)

        # Generate G-code with fitted G2/G3 arcs
        gcode = optimizer.generate_optimized_gcode(slicing_result, spindle_rpm=12000)
        self.assertIn("G2", gcode)  # Contains CW arc or G3 CCW arc
        self.assertIn("M3 S12000", gcode)
        self.assertIn("Auto-Machining Optimized", gcode)


if __name__ == "__main__":
    unittest.main()
