import unittest
import shapely.geometry as sg
from engine.slicer.stock import StockBlock
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.gcode.generator import GCodeGenerator


class TestGCodeGenerator(unittest.TestCase):

    def test_gcode_syntax_and_flow(self):
        stock = StockBlock(width_x=80.0, length_y=80.0, thickness_z=10.0)
        tool = CuttingTool(
            tool_id=1, name='Test Tool', tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175, stepdown_mm=2.0
        )
        slicer = SubtractiveSlicer(stock=stock, tool=tool)

        # Single circular pocket
        pocket = sg.Point(40.0, 40.0).buffer(15.0)
        result = slicer.slice_25d_pockets_and_profiles(
            pockets=[pocket], holes=[], perimeter=None, target_depth_mm=4.0
        )

        gen = GCodeGenerator(dialect="grbl")
        gcode = gen.generate(result, spindle_rpm=15000, program_name="TEST_JOB")

        self.assertIn("G21", gcode)
        self.assertIn("G90", gcode)
        self.assertIn("M3 S15000", gcode)
        self.assertIn("G0 Z", gcode)
        self.assertIn("G1", gcode)
        self.assertIn("M5", gcode)
        self.assertIn("M30", gcode)


if __name__ == "__main__":
    unittest.main()
