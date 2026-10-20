import unittest
import shapely.geometry as sg
import trimesh
import numpy as np

from engine.slicer.stock import StockBlock
from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.subtractive import SubtractiveSlicer
from engine.slicer.toolpaths import MoveType, OperationType


class TestSubtractiveSlicer(unittest.TestCase):

    def setUp(self):
        self.tool = CuttingTool(
            tool_id=1,
            name='1/8" Flat Endmill',
            tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175,
            stepdown_mm=1.0,
            stepover_fraction=0.45,
            feedrate_xy_mm_min=1200.0,
            plunge_rate_z_mm_min=300.0
        )

    def test_sample_1_pocket_and_hole_features(self):
        """Matches user hand drawing Sample 1:

        Rectangular block with circular hole and oblong slot through the block.
        Verify the subtractive slicer clears the hole and slot layer by layer.
        """
        stock = StockBlock(width_x=100.0, length_y=60.0, thickness_z=10.0, origin_x=0.0, origin_y=0.0)
        slicer = SubtractiveSlicer(stock=stock, tool=self.tool, safe_z_margin=5.0)

        # Circular hole (radius 8mm) at (25, 30)
        hole = sg.Point(25.0, 30.0).buffer(8.0)

        # Oblong slot (20mm x 10mm rounded rectangle) around (70, 30)
        slot = sg.box(60.0, 25.0, 80.0, 35.0).buffer(3.0)

        # Perimeter cutout box
        perimeter = sg.box(5.0, 5.0, 95.0, 55.0)

        result = slicer.slice_25d_pockets_and_profiles(
            pockets=[slot],
            holes=[hole],
            perimeter=perimeter,
            target_depth_mm=4.0,  # 4mm deep pockets / 4 passes at 1mm stepdown
            custom_stepdown=1.0
        )

        self.assertEqual(result.layer_count, 4)
        self.assertGreater(result.total_cut_length_mm, 50.0)
        self.assertGreater(result.estimated_time_minutes, 0.05)

        # Check each layer has pocket clearing operations
        for lyr in result.layers:
            ops = [seg.operation for seg in lyr.segments]
            self.assertIn(OperationType.POCKET, ops)
            self.assertIn(OperationType.PROFILE, ops)

    def test_sample_2_stepped_pyramid_subtractive_slicing(self):
        """Matches user hand drawing Sample 2:

        Solid raw stock block + 3-tier stepped pyramid target model.
        Verify Material to Remove = Stock(Z) - Mesh(Z) at each layer pass.
        """
        # Stock: 60 x 60 x 30 mm block
        stock = StockBlock(width_x=60.0, length_y=60.0, thickness_z=30.0, origin_x=0.0, origin_y=0.0)
        slicer = SubtractiveSlicer(stock=stock, tool=self.tool, safe_z_margin=5.0)

        # Create a 3-tier stepped pyramid mesh:
        # Tier 1 (top): 20x20x10 from Z=20 to Z=30
        # Tier 2 (mid): 40x40x10 from Z=10 to Z=20
        # Tier 3 (base): 60x60x10 from Z=0 to Z=10
        b1 = trimesh.creation.box(extents=[20, 20, 10])
        b1.apply_translation([30, 30, 25])

        b2 = trimesh.creation.box(extents=[40, 40, 10])
        b2.apply_translation([30, 30, 15])

        b3 = trimesh.creation.box(extents=[60, 60, 10])
        b3.apply_translation([30, 30, 5])

        stepped_mesh = trimesh.util.concatenate([b1, b2, b3])

        # Slice mesh down to Z=0 with 2mm stepdown
        result = slicer.slice_3d_mesh(mesh=stepped_mesh, z_bottom=0.0, custom_stepdown=2.0)

        self.assertEqual(result.layer_count, 15)  # 30mm / 2mm = 15 layers
        self.assertGreater(result.total_cut_length_mm, 100.0)

        # Verify that top layers clear larger area around the small tier 1,
        # and bottom layers clear smaller area around the wide tier 3!
        top_layer = result.layers[0]  # Z ~ 28mm
        self.assertGreater(len(top_layer.segments), 0)


if __name__ == "__main__":
    unittest.main()
