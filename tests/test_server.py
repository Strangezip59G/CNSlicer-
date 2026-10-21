import unittest
import json
from server.app import app


class TestServerEndpoints(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_index_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CNC Milling Slicer", res.data)

    def test_lidar_scan_endpoint(self):
        res = self.client.post("/api/lidar/scan", data=json.dumps({
            "bed_w": 400, "bed_l": 400,
            "stock_x": 80, "stock_y": 70,
            "stock_w": 100, "stock_l": 80, "stock_t": 20
        }), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["detected"])
        self.assertAlmostEqual(data["width"], 100, delta=10)

    def test_slice_sample1_endpoint(self):
        res = self.client.post("/api/slice", data=json.dumps({
            "model_type": "sample1",
            "stock_w": 100, "stock_l": 80, "stock_t": 20,
            "stepdown": 2.0, "stepover": 0.45, "tool_d": 3.175
        }), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreater(data["layer_count"], 0)
        self.assertGreater(data["cut_distance_mm"], 0)

    def test_gcode_download_endpoint(self):
        # First slice
        self.client.post("/api/slice", data=json.dumps({
            "model_type": "sample1",
            "stock_w": 100, "stock_l": 80, "stock_t": 10,
            "stepdown": 2.0, "stepover": 0.45, "tool_d": 3.175
        }), content_type="application/json")

        # Then download gcode
        res = self.client.get("/api/gcode/download")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"G21", res.data)
        self.assertIn(b"M3 S16000", res.data)
        self.assertIn(b"M30", res.data)

    def test_sample_pyramid_and_gcode_text(self):
        res = self.client.get("/api/model/sample_pyramid")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["metadata"]["width"], 60.0, delta=1.0)

        # Slice this 3D model
        slice_res = self.client.post("/api/slice", data=json.dumps({
            "model_type": "custom_stl",
            "stock_w": 80, "stock_l": 80, "stock_t": 35,
            "stepdown": 3.0, "stepover": 0.45, "tool_d": 3.175
        }), content_type="application/json")
        self.assertEqual(slice_res.status_code, 200)
        slice_data = slice_res.get_json()
        self.assertTrue(slice_data["success"])
        self.assertGreater(slice_data["layer_count"], 5)

        # Inspect G-code text
        gcode_res = self.client.get("/api/gcode/text")
        self.assertEqual(gcode_res.status_code, 200)
        gcode_data = gcode_res.get_json()
        self.assertIn("G21", gcode_data["gcode"])
        self.assertGreater(gcode_data["lines"], 20)

    def test_transforms_and_advanced_slicing_options(self):
        # First load sample pyramid
        self.client.get("/api/model/sample_pyramid")

        # Slice with rotation, scale, position offset, zigzag clearing, stock allowance, and holding tabs
        slice_res = self.client.post("/api/slice", data=json.dumps({
            "model_type": "custom_stl",
            "stock_w": 90, "stock_l": 90, "stock_t": 35,
            "stepdown": 3.0, "stepover": 0.5, "tool_d": 3.175,
            "pos_x": 5.0, "pos_y": -5.0, "pos_z": 0.0,
            "rot_x": 0.0, "rot_y": 0.0, "rot_z": 45.0,
            "scale_x": 0.8, "scale_y": 0.8, "scale_z": 0.8,
            "clearing_pattern": "zigzag",
            "stock_to_leave": 0.5,
            "tabs_count": 4,
            "tab_width": 5.0,
            "tab_height": 2.0
        }), content_type="application/json")
        self.assertEqual(slice_res.status_code, 200)
        slice_data = slice_res.get_json()
        self.assertTrue(slice_data["success"])
        self.assertGreater(slice_data["layer_count"], 3)
        self.assertGreater(slice_data["cut_distance_mm"], 50)

    def test_layflat_endpoint(self):
        # Load sample pyramid
        self.client.get("/api/model/sample_pyramid")
        # Call layflat
        res = self.client.post("/api/model/layflat")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("metadata", data)
        self.assertIn("vertices", data)
        self.assertIn("faces", data)

    def test_analyze_model_endpoint(self):
        self.client.get("/api/model/sample_pyramid")
        res = self.client.post("/api/model/analyze", data=json.dumps({
            "margin_xy": 6.0, "margin_z_top": 2.0
        }), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["width_x"], 60.0, delta=1.0)
        self.assertIn("recommended_box", data)
        self.assertIn("recommended_cylinder", data)
        self.assertIn("recommended_plate", data)
        self.assertIn("silhouette_exterior", data)
        self.assertIn("feature_edges", data)

    def test_slice_with_cylinder_stock_and_lock_to_model(self):
        self.client.get("/api/model/sample_pyramid")
        res = self.client.post("/api/slice", data=json.dumps({
            "model_type": "custom_stl",
            "stock_shape": "cylinder",
            "stock_d": 90.0,
            "stock_t": 35.0,
            "stock_lock_to_model": True,
            "pos_x": 10.0,
            "pos_y": 15.0,
            "stepdown": 3.0,
            "tool_d": 3.175
        }), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreater(data["layer_count"], 5)
        self.assertGreater(data["cut_distance_mm"], 100.0)


if __name__ == "__main__":
    unittest.main()
