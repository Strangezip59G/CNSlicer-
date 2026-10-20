import unittest
import numpy as np
from engine.lidar.mock_sensor import MockLidarSensor
from engine.lidar.scanner import ToolheadLidarScanner
from engine.lidar.heightmap import Heightmap


class TestToolheadLidar(unittest.TestCase):

    def test_auto_stock_discovery_anywhere_on_bed(self):
        """Simulate a raw stock block placed at an arbitrary location on a 400x400 bed.

        Verify the toolhead LiDAR scanner auto-discovers its exact location and dimensions.
        """
        sensor = MockLidarSensor(bed_width=400.0, bed_length=400.0, spoilboard_z=0.0, noise_std_mm=0.01)

        # User clamps a 120mm x 80mm x 25mm block at arbitrary position (X=145, Y=210)
        sensor.place_virtual_stock(
            x_min=145.0, y_min=210.0,
            width=120.0, length=80.0,
            thickness=25.0,
            tilt_x_mm=0.15, tilt_y_mm=-0.10
        )

        # Toolhead LiDAR sweeps the bed
        scan_pts = sensor.simulate_sweep(step_x=5.0, step_y=5.0)
        self.assertGreater(len(scan_pts), 1000)

        scanner = ToolheadLidarScanner(bed_width=400.0, bed_length=400.0, spoilboard_z=0.0)
        detected = scanner.process_bed_scan(scan_pts, height_threshold_mm=2.0)

        self.assertIsNotNone(detected)
        # Should detect X within 5mm grid resolution
        self.assertAlmostEqual(detected.x_min, 145.0, delta=5.5)
        self.assertAlmostEqual(detected.x_max, 265.0, delta=5.5)
        self.assertAlmostEqual(detected.y_min, 210.0, delta=5.5)
        self.assertAlmostEqual(detected.y_max, 290.0, delta=5.5)
        self.assertAlmostEqual(detected.thickness, 25.0, delta=0.5)

        # Check heightmap auto-leveling
        hmap = detected.heightmap
        self.assertIsNotNone(hmap)
        # Height at center should be ~25mm
        cx, cy = detected.center
        center_z = hmap.get_surface_z(cx, cy)
        self.assertAlmostEqual(center_z, 25.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
