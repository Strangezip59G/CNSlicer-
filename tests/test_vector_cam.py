from __future__ import annotations
import math
import unittest
import numpy as np
import cv2
import shapely.geometry as sg

from engine.slicer.tool import CuttingTool, ToolType
from engine.slicer.toolpaths import MoveType, OperationType
from engine.slicer.vector_cam import (
    VectorOpMode, HoldingTabsConfig, VectorEntity, VectorProject,
    SVGParser, DXFParser, RasterTracer, VectorCAMGenerator
)


class TestVectorCAM(unittest.TestCase):
    """Unit test suite for 2D Vector CAM (SVG/DXF) & Image-to-Vector Tracing Beta Engine."""

    def test_svg_parser_paths_and_shapes(self):
        svg_content = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100">
            <rect x="10" y="10" width="80" height="80" rx="0" ry="0" />
            <circle cx="50" cy="50" r="15" />
            <path d="M 20,20 L 30,20 L 30,30 L 20,30 Z" />
            <line x1="5" y1="5" x2="95" y2="95" />
        </svg>"""

        project = SVGParser.parse_svg_string(svg_content)
        self.assertGreaterEqual(len(project.entities), 2)
        self.assertGreater(project.width, 50.0)
        self.assertGreater(project.height, 50.0)

        # Test project scaling and centering
        project.scale_to_dimensions(60.0, 60.0, keep_aspect=True)
        self.assertAlmostEqual(project.width, 60.0, delta=2.0)
        project.center_at(100.0, 100.0)
        b = project.bounds
        self.assertAlmostEqual((b[0] + b[2]) / 2.0, 100.0, delta=1.0)
        self.assertAlmostEqual((b[1] + b[3]) / 2.0, 100.0, delta=1.0)

    def test_svg_bezier_and_arc_parsing(self):
        svg_curves = """<svg width="50mm" height="50mm" viewBox="0 0 50 50">
            <path d="M 10 10 C 20 20, 40 20, 40 10 S 60 0, 50 20 Q 30 40, 20 30 A 10 10 0 0 1 10 10 Z" />
        </svg>"""
        project = SVGParser.parse_svg_string(svg_curves)
        self.assertEqual(len(project.entities), 1)
        entity = project.entities[0]
        self.assertTrue(entity.is_closed)
        self.assertIsInstance(entity.geometry, (sg.Polygon, sg.MultiPolygon))

    def test_dxf_parser_lines_circles_polylines(self):
        dxf_sample = """0
SECTION
2
ENTITIES
0
LINE
10
0.0
20
0.0
11
50.0
21
0.0
0
CIRCLE
10
25.0
20
25.0
40
10.0
0
LWPOLYLINE
70
1
90
4
10
10.0
20
10.0
10
40.0
20
10.0
10
40.0
20
40.0
10
10.0
20
40.0
0
ENDSEC
0
EOF"""
        # With assemble_holes=False: returns raw entities (3 entities)
        project_raw = DXFParser.parse_dxf_text(dxf_sample, assemble_holes=False)
        self.assertEqual(len(project_raw.entities), 3)

        # With assemble_holes=True (default): circle inside polyline becomes interior hole (2 entities)
        project_assembled = DXFParser.parse_dxf_text(dxf_sample, assemble_holes=True)
        self.assertEqual(len(project_assembled.entities), 2)
        poly_with_hole = [e for e in project_assembled.entities if isinstance(e.geometry, sg.Polygon)][0]
        self.assertEqual(len(poly_with_hole.geometry.interiors), 1)

    def test_raster_image_tracer_with_holes(self):
        # Create a synthetic image with a solid square and an inner circle hole on a white canvas
        img = np.ones((200, 200, 3), dtype=np.uint8) * 255
        cv2.rectangle(img, (30, 30), (170, 170), (0, 0, 0), -1)
        cv2.circle(img, (100, 100), 30, (255, 255, 255), -1)

        _, buf = cv2.imencode(".png", img)
        project = RasterTracer.trace_image_bytes(
            buf.tobytes(),
            use_otsu=True,
            approx_epsilon=1.0,
            target_width_mm=80.0
        )

        self.assertGreaterEqual(len(project.entities), 1)
        entity = project.entities[0]
        self.assertTrue(entity.is_closed)
        self.assertIsInstance(entity.geometry, (sg.Polygon, sg.MultiPolygon))
        # Hole subtraction: area should be less than the solid outer square
        outer_area = (80.0 * (140.0 / 200.0)) ** 2
        self.assertLess(entity.geometry.area, outer_area)

    def test_vector_cam_toolpath_engrave(self):
        tool = CuttingTool(
            tool_id=1, name="1/8 Flat Endmill", tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175, stepdown_mm=1.0, feedrate_xy_mm_min=1200.0, plunge_rate_z_mm_min=300.0
        )
        line = sg.LineString([(0, 0), (50, 0), (50, 50), (0, 50)])
        entity = VectorEntity(id="e1", geometry=line, is_closed=False, operation=VectorOpMode.ENGRAVE)
        project = VectorProject(entities=[entity])

        result = VectorCAMGenerator.generate_toolpaths(
            project=project,
            operation=VectorOpMode.ENGRAVE,
            tool=tool,
            target_depth_mm=2.0,
            stepdown_mm=1.0
        )

        self.assertEqual(result.layer_count, 2)
        self.assertEqual(result.layers[0].nominal_z, -1.0)
        self.assertEqual(result.layers[1].nominal_z, -2.0)
        self.assertGreater(result.total_cut_length_mm, 200.0)

    def test_vector_cam_toolpath_profile_outside_with_tabs(self):
        tool = CuttingTool(
            tool_id=1, name="1/8 Flat Endmill", tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175, stepdown_mm=1.0, feedrate_xy_mm_min=1200.0, plunge_rate_z_mm_min=300.0
        )
        box = sg.box(10, 10, 50, 50)
        entity = VectorEntity(id="b1", geometry=box, is_closed=True, operation=VectorOpMode.PROFILE_OUTSIDE)
        project = VectorProject(entities=[entity])

        tabs = HoldingTabsConfig(enabled=True, count=4, width_mm=5.0, height_mm=1.5)
        result = VectorCAMGenerator.generate_toolpaths(
            project=project,
            operation=VectorOpMode.PROFILE_OUTSIDE,
            tool=tool,
            target_depth_mm=3.0,
            stepdown_mm=1.0,
            tabs=tabs
        )

        self.assertEqual(result.layer_count, 3)
        deepest_layer = result.layers[-1]
        z_coords = [p.z for seg in deepest_layer.segments for p in seg.points if p.move_type == MoveType.CUT]
        # Some cut moves should be at -3.0, and tab bridges should be at -1.5
        has_tab_elevation = any(abs(z - (-1.5)) < 0.05 for z in z_coords)
        self.assertTrue(has_tab_elevation, "Deepest pass must contain elevated holding tab bridges")

    def test_vector_cam_toolpath_pocket(self):
        tool = CuttingTool(
            tool_id=1, name="1/8 Flat Endmill", tool_type=ToolType.FLAT_ENDMILL,
            diameter_mm=3.175, stepdown_mm=1.5, stepover_fraction=0.4
        )
        box = sg.box(0, 0, 40, 40)
        entity = VectorEntity(id="p1", geometry=box, is_closed=True, operation=VectorOpMode.POCKET)
        project = VectorProject(entities=[entity])

        result = VectorCAMGenerator.generate_toolpaths(
            project=project,
            operation=VectorOpMode.POCKET,
            tool=tool,
            target_depth_mm=1.5,
            stepdown_mm=1.5
        )

        self.assertEqual(result.layer_count, 1)
        seg_count = len(result.layers[0].segments)
        self.assertGreater(seg_count, 2, "Pocket clearing must generate multiple concentric offset loops")


class TestVectorCAMServerEndpoints(unittest.TestCase):
    """Integration test suite for Vector CAM Flask endpoints."""

    def setUp(self):
        from server.app import app
        self.app = app
        self.client = app.test_client()

    def test_load_sample_vector_endpoints(self):
        # 1. Load gear sample
        resp = self.client.post("/api/vector/sample", json={"sample_type": "gear"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertGreater(data["project"]["entity_count"], 0)
        self.assertIn("spur_gear", data["project"]["source_filename"])

        # 2. Load sketch sample
        resp = self.client.post("/api/vector/sample", json={"sample_type": "sketch"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["project"]["source_type"], "raster_trace")

        # 3. Load default logo sample
        resp = self.client.post("/api/vector/sample", json={"sample_type": "logo"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

    def test_vector_transform_and_slice_endpoints(self):
        # Load sample logo
        self.client.post("/api/vector/sample", json={"sample_type": "logo"})

        # Transform (scale and move)
        t_resp = self.client.post("/api/vector/transform", json={
            "center_x": 100.0,
            "center_y": 80.0,
            "target_width": 50.0,
            "target_height": 50.0
        })
        self.assertEqual(t_resp.status_code, 200)
        t_data = t_resp.get_json()
        self.assertTrue(t_data["success"])

        # Slice vector into toolpaths
        s_resp = self.client.post("/api/vector/slice", json={
            "operation": "profile_outside",
            "tool_diameter": 3.175,
            "target_depth": 3.0,
            "stepdown": 1.0,
            "tabs_enabled": True,
            "tab_count": 4,
            "tab_width": 4.0,
            "tab_height": 1.5
        })
        self.assertEqual(s_resp.status_code, 200)
        s_data = s_resp.get_json()
        self.assertTrue(s_data["success"])
        self.assertGreaterEqual(s_data["layer_count"], 3)
        self.assertGreater(s_data["cut_distance_mm"], 0.0)
        self.assertIn("layers", s_data)

        # Verify G-code generation from vector slicing
        g_resp = self.client.get("/api/gcode/text")
        self.assertEqual(g_resp.status_code, 200)
        g_text = g_resp.get_data(as_text=True)
        self.assertIn("G1", g_text)
        self.assertIn("G0", g_text)


if __name__ == "__main__":
    unittest.main()
