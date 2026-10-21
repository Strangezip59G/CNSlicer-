import unittest
from engine.slicer.auto_pilot import AutoPilot
from engine.slicer.analyzer import DesignAnalysisResult
from engine.slicer.machine import STANDARD_CNC_MACHINES

def make_sample_analysis(
    wx=60.0, ly=60.0, hz=30.0, vol=36.0, circ=0.78, shape="box"
) -> DesignAnalysisResult:
    return DesignAnalysisResult(
        width_x=wx,
        length_y=ly,
        height_z=hz,
        min_x=-wx/2,
        max_x=wx/2,
        min_y=-ly/2,
        max_y=ly/2,
        min_z=0.0,
        max_z=hz,
        centroid=[0.0, 0.0, hz/2],
        volume_cm3=vol,
        surface_area_cm2=120.0,
        footprint_area_mm2=wx * ly,
        perimeter_length_mm=2 * (wx + ly),
        circularity=circ,
        detected_shape=shape,
        shape_description="Test Part",
        triangle_count=36,
        vertex_count=20,
        is_watertight=True,
        min_cylinder_diameter_mm=85.0,
        min_cylinder_height_mm=hz,
        recommended_box={"width": wx + 10, "length": ly + 10, "thickness": hz + 2},
        recommended_cylinder={"diameter": 95.0, "thickness": hz + 2},
        recommended_plate={"width": wx + 10, "length": ly + 10, "thickness": hz + 2},
        recommended_round_plate={"diameter": 95.0, "thickness": hz + 2},
    )

class TestAutoPilot(unittest.TestCase):
    def test_pyramid_on_genmitsu_3018(self):
        analysis = make_sample_analysis(wx=60.0, ly=60.0, hz=30.0, vol=36.0, circ=0.78)
        machine = STANDARD_CNC_MACHINES["genmitsu_3018"]
        rec = AutoPilot.compute_recommendations(analysis, machine)

        self.assertEqual(rec.tool_key, "flat_1_8")
        self.assertEqual(rec.clearing_pattern, "concentric")
        self.assertLessEqual(rec.spindle_rpm, machine.spindle_max_rpm)
        self.assertEqual(rec.spindle_rpm, 10000)
        self.assertGreaterEqual(rec.feed_xy_mm_min, 400.0)
        self.assertLessEqual(rec.feed_xy_mm_min, machine.rapid_speed)
        self.assertGreater(rec.stepdown_mm, 0)
        self.assertGreater(len(rec.reasons), 2)

    def test_large_part_on_shapeoko_4(self):
        analysis = make_sample_analysis(wx=200.0, ly=180.0, hz=40.0, vol=140.0, circ=0.70)
        machine = STANDARD_CNC_MACHINES["shapeoko_4"]
        rec = AutoPilot.compute_recommendations(analysis, machine)

        self.assertEqual(rec.tool_key, "flat_6mm")
        self.assertGreater(rec.spindle_rpm, 10000)
        self.assertGreater(rec.feed_xy_mm_min, 1000.0)
        self.assertEqual(rec.tabs_count, 4)

    def test_elongated_plate_selects_zigzag(self):
        analysis = make_sample_analysis(wx=180.0, ly=40.0, hz=10.0, vol=15.0, circ=0.35, shape="plate")
        machine = STANDARD_CNC_MACHINES["genmitsu_4030"]
        rec = AutoPilot.compute_recommendations(analysis, machine)

        self.assertEqual(rec.clearing_pattern, "zigzag")
        self.assertEqual(rec.stepover_pct, 55)

    def test_shallow_relief_selects_v_bit(self):
        analysis = make_sample_analysis(wx=50.0, ly=50.0, hz=2.5, vol=3.0, circ=0.75)
        machine = STANDARD_CNC_MACHINES["nomad_3"]
        rec = AutoPilot.compute_recommendations(analysis, machine)

        self.assertEqual(rec.tool_key, "v_bit_60")
        self.assertEqual(rec.stock_to_leave_mm, 0.0)

if __name__ == "__main__":
    unittest.main()
