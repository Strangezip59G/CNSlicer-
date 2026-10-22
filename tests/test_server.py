import unittest
import json
from server.app import app


class TestServerEndpoints(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_index_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CNSlicer", res.data)

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
        self.assertIn(b"M3 S", res.data)
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

        # Verify G-code diagnostics
        verify_res = self.client.get("/api/gcode/verify")
        self.assertEqual(verify_res.status_code, 200)
        verify_data = verify_res.get_json()
        self.assertTrue(verify_data["success"])
        self.assertTrue(verify_data["report"]["is_valid"])
        self.assertEqual(verify_data["report"]["status"], "PASS")
        self.assertGreater(verify_data["report"]["cutting_blocks"], 10)
        self.assertGreater(verify_data["report"]["spindle_rpm"], 0)

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

    def test_machines_list_and_select(self):
        # 1. Fetch available machine profiles
        res = self.client.get("/api/machines")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertGreaterEqual(len(data["machines"]), 8)
        machine_ids = [m["id"] for m in data["machines"]]
        self.assertIn("genmitsu_3018", machine_ids)
        self.assertIn("shapeoko_4", machine_ids)
        self.assertIn("nomad_3", machine_ids)
        self.assertIn("bantam_desktop", machine_ids)
        self.assertIn("haas_vf2", machine_ids)
        self.assertIn("datron_neo", machine_ids)
        self.assertIn("brother_speedio", machine_ids)
        self.assertIn("dmg_mori_cmx600v", machine_ids)

        # 2. Select Shapeoko 4
        sel_res = self.client.post("/api/machines/select", data=json.dumps({
            "machine_id": "shapeoko_4"
        }), content_type="application/json")
        self.assertEqual(sel_res.status_code, 200)
        sel_data = sel_res.get_json()
        self.assertTrue(sel_data["success"])
        self.assertEqual(sel_data["active_machine"]["name"], "Shapeoko 4 Standard")
        self.assertEqual(sel_data["active_machine"]["bed_width_x"], 444.0)
        self.assertEqual(sel_data["active_machine"]["bed_length_y"], 444.0)

        # 3. Select Industrial Machine: Haas VF-2
        haas_res = self.client.post("/api/machines/select", data=json.dumps({
            "machine_id": "haas_vf2"
        }), content_type="application/json")
        self.assertEqual(haas_res.status_code, 200)
        haas_data = haas_res.get_json()
        self.assertTrue(haas_data["success"])
        self.assertEqual(haas_data["active_machine"]["name"], "Haas VF-2 (Industrial VMC)")
        self.assertEqual(haas_data["active_machine"]["bed_width_x"], 762.0)
        self.assertEqual(haas_data["active_machine"]["max_z_travel"], 508.0)
        self.assertEqual(haas_data["active_machine"]["spindle_max_rpm"], 12000)

        # 3. Select custom machine
        cust_res = self.client.post("/api/machines/select", data=json.dumps({
            "machine_id": "custom_mill",
            "bed_width_x": 500.0,
            "bed_length_y": 500.0,
            "max_z_travel": 120.0,
            "spindle_max_rpm": 20000
        }), content_type="application/json")
        self.assertEqual(cust_res.status_code, 200)
        cust_data = cust_res.get_json()
        self.assertEqual(cust_data["active_machine"]["bed_width_x"], 500.0)

    def test_optimizer_options_and_analyze_endpoints(self):
        # 1. Test options endpoint
        res = self.client.get("/api/optimizer/options")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("profiles", data)
        self.assertIn("materials", data)
        self.assertIn("finishes", data)
        self.assertIn("balanced", data["profiles"])
        self.assertIn("aluminum_6061", data["materials"])

        # 2. Load model
        self.client.get("/api/model/sample_pyramid")

        # 3. Test analyze endpoint
        an_res = self.client.post("/api/optimizer/analyze", data=json.dumps({
            "profile": "fastest",
            "material": "wood_mdf",
            "finish": "standard"
        }), content_type="application/json")
        self.assertEqual(an_res.status_code, 200)
        an_data = an_res.get_json()
        self.assertTrue(an_data["success"])
        self.assertEqual(an_data["profile"], "fastest")
        self.assertIn("improvements", an_data)
        self.assertIn("rationales", an_data)

    def test_optimizer_apply_endpoint(self):
        # Load model
        self.client.get("/api/model/sample_pyramid")

        # Apply optimized parameters and slice
        app_res = self.client.post("/api/optimizer/apply", data=json.dumps({
            "profile": "balanced",
            "material": "aluminum_6061",
            "finish": "fine",
            "stock_shape": "box",
            "gcode_opts": {"arc_fitting": True, "micro_filter": True}
        }), content_type="application/json")
        self.assertEqual(app_res.status_code, 200)
        app_data = app_res.get_json()
        self.assertTrue(app_data["success"])
        self.assertGreater(app_data["layer_count"], 0)
        self.assertIn("gcode_stats", app_data)
        self.assertGreaterEqual(app_data["gcode_stats"]["fitted_arcs_count"], 0)

        # Verify optimized G-code is served by gcode/text
        gc_res = self.client.get("/api/gcode/text")
        self.assertEqual(gc_res.status_code, 200)
        gc_data = gc_res.get_json()
        self.assertTrue(gc_data.get("is_optimized"))
        self.assertIn("Auto-Machining Optimized", gc_data["gcode"])


if __name__ == "__main__":
    unittest.main()
