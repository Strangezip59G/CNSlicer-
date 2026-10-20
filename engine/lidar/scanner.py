from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from engine.lidar.heightmap import Heightmap


@dataclass
class DetectedStock:
    """Represents a physical stock material block discovered automatically by toolhead LiDAR."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    thickness: float  # Nominal measured thickness (Z height above spoilboard)
    heightmap: Heightmap
    rotation_angle_deg: float = 0.0  # Skew angle relative to machine X axis
    spoilboard_z: float = 0.0

    @property
    def width(self) -> float:
        """Stock width along X axis in mm."""
        return self.x_max - self.x_min

    @property
    def length(self) -> float:
        """Stock length along Y axis in mm."""
        return self.y_max - self.y_min

    @property
    def center(self) -> tuple[float, float]:
        """Center coordinate (X, Y) of the stock on the bed."""
        return (self.x_min + self.width / 2.0, self.y_min + self.length / 2.0)

    @property
    def top_z(self) -> float:
        """Absolute top Z height above spoilboard."""
        return self.spoilboard_z + self.thickness


class ToolheadLidarScanner:
    """Processes toolhead-mounted LiDAR sweep scans to automatically discover stock location,

    dimensions, skew, and top surface topology on the machine bed.
    """

    def __init__(self, bed_width: float = 400.0, bed_length: float = 400.0, spoilboard_z: float = 0.0):
        self.bed_width = float(bed_width)
        self.bed_length = float(bed_length)
        self.spoilboard_z = float(spoilboard_z)

    def process_bed_scan(
        self,
        scan_points: np.ndarray,
        height_threshold_mm: float = 2.0,
        surface_grid_step_mm: float = 10.0,
    ) -> DetectedStock | None:
        """Process a point cloud of toolhead LiDAR readings collected while sweeping the bed.

        scan_points: Nx3 numpy array of [X, Y, Z_measured] in machine coordinates.
        height_threshold_mm: minimum height above spoilboard to consider as stock material.
        """
        if len(scan_points) == 0:
            return None

        # Filter points that are above the spoilboard by at least height_threshold_mm
        stock_mask = scan_points[:, 2] > (self.spoilboard_z + height_threshold_mm)
        stock_points = scan_points[stock_mask]

        if len(stock_points) < 4:
            return None  # Not enough points detected to define a stock block

        x_pts = stock_points[:, 0]
        y_pts = stock_points[:, 1]
        z_pts = stock_points[:, 2]

        x_min, x_max = float(np.min(x_pts)), float(np.max(x_pts))
        y_min, y_max = float(np.min(y_pts)), float(np.max(y_pts))
        nominal_thickness = float(np.median(z_pts) - self.spoilboard_z)

        # Build surface heightmap grid
        grid_xs = np.arange(x_min, x_max + surface_grid_step_mm * 0.5, surface_grid_step_mm)
        grid_ys = np.arange(y_min, y_max + surface_grid_step_mm * 0.5, surface_grid_step_mm)

        if len(grid_xs) < 2:
            grid_xs = np.array([x_min, x_max])
        if len(grid_ys) < 2:
            grid_ys = np.array([y_min, y_max])

        # Fill heightmap by nearest neighbor / inverse distance weighting from scan_points
        z_matrix = np.zeros((len(grid_ys), len(grid_xs)), dtype=float)
        for j, gy in enumerate(grid_ys):
            for i, gx in enumerate(grid_xs):
                dists_sq = (x_pts - gx) ** 2 + (y_pts - gy) ** 2
                closest_idx = np.argmin(dists_sq)
                z_matrix[j, i] = z_pts[closest_idx]

        hmap = Heightmap(
            x_coords=grid_xs,
            y_coords=grid_ys,
            z_matrix=z_matrix,
            reference_z=float(np.median(z_pts))
        )

        return DetectedStock(
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            thickness=nominal_thickness,
            heightmap=hmap,
            rotation_angle_deg=0.0,
            spoilboard_z=self.spoilboard_z
        )

    def generate_sweep_gcode(
        self,
        scan_step_y: float = 25.0,
        feedrate_mm_min: float = 3000.0,
        safe_z: float = 30.0
    ) -> list[str]:
        """Generate a fast raster sweep G-code sequence for the machine head to scan the full bed."""
        lines = [
            "; --- TOOLHEAD LIDAR BED SCAN ---",
            "G90 ; Absolute positioning",
            "G21 ; Millimeter units",
            f"G0 Z{safe_z:.2f} ; Safe travel height",
            "G0 X0 Y0 ; Move to bed origin"
        ]
        y = 0.0
        direction = 1
        while y <= self.bed_length:
            target_x = self.bed_width if direction == 1 else 0.0
            lines.append(f"G1 Y{y:.2f} F{feedrate_mm_min:.0f}")
            lines.append(f"G1 X{target_x:.2f} F{feedrate_mm_min:.0f}")
            direction *= -1
            y += scan_step_y

        lines.append(f"G0 Z{safe_z:.2f}")
        lines.append("G0 X0 Y0")
        lines.append("; --- END SCAN ---")
        return lines
