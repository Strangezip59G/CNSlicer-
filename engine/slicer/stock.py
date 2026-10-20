from __future__ import annotations
from dataclasses import dataclass, field
import shapely.geometry as sg
from engine.lidar.heightmap import Heightmap


@dataclass
class ClampObstacle:
    """Represents a workholding clamp or screw to avoid during machining."""
    name: str
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    height_z_mm: float  # Top height of the clamp above spoilboard

    @property
    def polygon(self) -> sg.Polygon:
        return sg.box(self.x_min, self.y_min, self.x_max, self.y_max)


@dataclass
class StockBlock:
    """Represents the raw material block clamped on the CNC bed."""
    width_x: float  # Dimension along X (mm)
    length_y: float  # Dimension along Y (mm)
    thickness_z: float  # Dimension along Z (mm)
    origin_x: float = 0.0  # Bottom-left X position on bed (mm)
    origin_y: float = 0.0  # Bottom-left Y position on bed (mm)
    spoilboard_z: float = 0.0  # Z plane of the spoilboard bed
    heightmap: Heightmap | None = None
    clamps: list[ClampObstacle] = field(default_factory=list)

    @property
    def x_min(self) -> float:
        return self.origin_x

    @property
    def x_max(self) -> float:
        return self.origin_x + self.width_x

    @property
    def y_min(self) -> float:
        return self.origin_y

    @property
    def y_max(self) -> float:
        return self.origin_y + self.length_y

    @property
    def top_z(self) -> float:
        """Nominal top surface Z coordinate (relative to spoilboard or machine WCS)."""
        return self.spoilboard_z + self.thickness_z

    @property
    def polygon(self) -> sg.Polygon:
        """2D planar footprint of the stock on the bed."""
        return sg.box(self.x_min, self.y_min, self.x_max, self.y_max)

    def contains_xy(self, x: float, y: float) -> bool:
        return (self.x_min <= x <= self.x_max) and (self.y_min <= y <= self.y_max)

    def get_surface_z(self, x: float, y: float) -> float:
        """Get local top surface height from LiDAR heightmap if available, else nominal top."""
        if self.heightmap is not None:
            return self.heightmap.get_surface_z(x, y)
        return self.top_z
