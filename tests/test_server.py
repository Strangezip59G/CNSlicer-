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


if __name__ == "__main__":
    unittest.main()
