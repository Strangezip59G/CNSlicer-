from __future__ import annotations
import numpy as np


class MockLidarSensor:
    """Simulates a toolhead LiDAR sensor measuring distance to bed and arbitrary stock material."""

    def __init__(
        self,
        bed_width: float = 400.0,
        bed_length: float = 400.0,
        spoilboard_z: float = 0.0,
        noise_std_mm: float = 0.02
    ):
        self.bed_width = bed_width
        self.bed_length = bed_length
        self.spoilboard_z = spoilboard_z
        self.noise_std_mm = noise_std_mm
        self.virtual_stocks: list[dict] = []

    def place_virtual_stock(
        self,
        x_min: float,
        y_min: float,
        width: float,
        length: float,
        thickness: float,
        tilt_x_mm: float = 0.0,
        tilt_y_mm: float = 0.0
    ):
        """Place a virtual stock block on the simulated machine bed."""
        self.virtual_stocks.append({
            "x_min": x_min,
            "x_max": x_min + width,
            "y_min": y_min,
            "y_max": y_min + length,
            "thickness": thickness,
            "tilt_x": tilt_x_mm,
            "tilt_y": tilt_y_mm
        })

    def measure_point(self, x: float, y: float) -> float:
        """Measure the physical surface height at machine position (x, y)."""
        z = self.spoilboard_z
        for stock in self.virtual_stocks:
            if stock["x_min"] <= x <= stock["x_max"] and stock["y_min"] <= y <= stock["y_max"]:
                dx = (x - stock["x_min"]) / max(1.0, stock["x_max"] - stock["x_min"])
                dy = (y - stock["y_min"]) / max(1.0, stock["y_max"] - stock["y_min"])
                tilt = dx * stock["tilt_x"] + dy * stock["tilt_y"]
                z = self.spoilboard_z + stock["thickness"] + tilt
                break

        if self.noise_std_mm > 0:
            z += np.random.normal(0, self.noise_std_mm)
        return float(z)

    def simulate_sweep(self, step_x: float = 5.0, step_y: float = 10.0, centered: bool | None = None) -> np.ndarray:
        """Simulate a raster sweep across the bed and return Nx3 array of [X, Y, Z]."""
        if centered is None:
            has_negative = any(s["x_min"] < 0 or s["y_min"] < 0 for s in self.virtual_stocks)
            centered = has_negative

        if centered:
            half_w = self.bed_width / 2.0
            half_l = self.bed_length / 2.0
            xs = np.arange(-half_w, half_w + step_x * 0.5, step_x)
            ys = np.arange(-half_l, half_l + step_y * 0.5, step_y)
        else:
            xs = np.arange(0, self.bed_width + step_x * 0.5, step_x)
            ys = np.arange(0, self.bed_length + step_y * 0.5, step_y)

        points = []
        for y in ys:
            for x in xs:
                z = self.measure_point(x, y)
                points.append([x, y, z])
        return np.array(points, dtype=float)
