from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np


@dataclass
class Heightmap:
    """Represents a measured 2D surface heightmap grid for auto-leveling & warp compensation."""
    x_coords: np.ndarray  # 1D array of X sampling positions (sorted)
    y_coords: np.ndarray  # 1D array of Y sampling positions (sorted)
    z_matrix: np.ndarray  # 2D array of shape (len(y_coords), len(x_coords)) with measured Z values
    reference_z: float = 0.0  # Nominal Z origin (e.g. top of stock at reference corner)

    @classmethod
    def flat(cls, x_min: float, x_max: float, y_min: float, y_max: float, z: float = 0.0) -> Heightmap:
        """Create an ideal flat heightmap."""
        xs = np.array([x_min, x_max], dtype=float)
        ys = np.array([y_min, y_max], dtype=float)
        zm = np.full((2, 2), z, dtype=float)
        return cls(x_coords=xs, y_coords=ys, z_matrix=zm, reference_z=z)

    @property
    def min_z(self) -> float:
        return float(np.min(self.z_matrix))

    @property
    def max_z(self) -> float:
        return float(np.max(self.z_matrix))

    @property
    def max_deviation(self) -> float:
        """Peak-to-valley flatness deviation (in mm)."""
        return self.max_z - self.min_z

    def needs_facing(self, tolerance_mm: float = 0.2) -> bool:
        """Returns True if the surface warp/tilt exceeds the acceptable tolerance."""
        return self.max_deviation > tolerance_mm

    def get_surface_z(self, x: float, y: float) -> float:
        """Bilinearly interpolate the surface Z height at coordinate (x, y)."""
        # Clamp to bounds
        cx = np.clip(x, self.x_coords[0], self.x_coords[-1])
        cy = np.clip(y, self.y_coords[0], self.y_coords[-1])

        # Find grid indices
        i = np.searchsorted(self.x_coords, cx) - 1
        j = np.searchsorted(self.y_coords, cy) - 1
        i = max(0, min(i, len(self.x_coords) - 2))
        j = max(0, min(j, len(self.y_coords) - 2))

        x0, x1 = self.x_coords[i], self.x_coords[i + 1]
        y0, y1 = self.y_coords[j], self.y_coords[j + 1]

        z00 = self.z_matrix[j, i]
        z10 = self.z_matrix[j, i + 1]
        z01 = self.z_matrix[j + 1, i]
        z11 = self.z_matrix[j + 1, i + 1]

        tx = 0.0 if (x1 == x0) else (cx - x0) / (x1 - x0)
        ty = 0.0 if (y1 == y0) else (cy - y0) / (y1 - y0)

        # Bilinear interpolation
        z_top = (1.0 - tx) * z00 + tx * z10
        z_bot = (1.0 - tx) * z01 + tx * z11
        return float((1.0 - ty) * z_top + ty * z_bot)

    def conformal_z(self, x: float, y: float, nominal_cut_depth: float) -> float:
        """Compute the adjusted Z cutting coordinate so that cutting depth is constant

        relative to the measured local warped surface.
        nominal_cut_depth is typically <= 0 (relative to reference_z).
        """
        surface_z = self.get_surface_z(x, y)
        delta_z = surface_z - self.reference_z
        return nominal_cut_depth + delta_z
