from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import shapely.geometry as sg
import shapely.affinity as sa
from engine.lidar.heightmap import Heightmap


class StockShape(str, Enum):
    BOX = "box"                     # Solid rectangular billet / cube
    PLATE = "plate"                 # Rectangular metal plate / sheet stock
    CYLINDER = "cylinder"           # Cylindrical bar / round stock / billet
    ROUND_PLATE = "round_plate"     # Cylindrical metal plate / circular blank / disc
    CONFORMING = "conforming"       # Minimal conforming stock outline following model silhouette


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
    """Represents the raw stock material clamped on the CNC bed.

    Supports rectangular blocks, metal plates, cylindrical billets, circular discs,
    and silhouette-conforming raw stock shapes.
    """
    shape: StockShape = StockShape.BOX
    width_x: float = 100.0          # Dimension along X (mm)
    length_y: float = 100.0         # Dimension along Y (mm)
    thickness_z: float = 20.0       # Dimension along Z (mm)
    diameter_d: float = 0.0         # Diameter for cylinder/disc (mm)
    origin_x: float = 0.0           # Bottom-left X position on bed (mm)
    origin_y: float = 0.0           # Bottom-left Y position on bed (mm)
    spoilboard_z: float = 0.0       # Z plane of the spoilboard bed
    conforming_polygon: sg.Polygon | sg.MultiPolygon | None = None
    heightmap: Heightmap | None = None
    clamps: list[ClampObstacle] = field(default_factory=list)

    def __post_init__(self):
        if self.diameter_d <= 0.0 and self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            self.diameter_d = max(self.width_x, self.length_y)

    @property
    def radius_r(self) -> float:
        dia = self.diameter_d if self.diameter_d > 0 else max(self.width_x, self.length_y)
        return dia / 2.0

    @property
    def center_x(self) -> float:
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            return self.origin_x + self.radius_r
        return self.origin_x + self.width_x / 2.0

    @property
    def center_y(self) -> float:
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            return self.origin_y + self.radius_r
        return self.origin_y + self.length_y / 2.0

    @property
    def x_min(self) -> float:
        if self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            return float(self.conforming_polygon.bounds[0])
        return self.origin_x

    @property
    def x_max(self) -> float:
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            return self.origin_x + self.radius_r * 2.0
        if self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            return float(self.conforming_polygon.bounds[2])
        return self.origin_x + self.width_x

    @property
    def y_min(self) -> float:
        if self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            return float(self.conforming_polygon.bounds[1])
        return self.origin_y

    @property
    def y_max(self) -> float:
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            return self.origin_y + self.radius_r * 2.0
        if self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            return float(self.conforming_polygon.bounds[3])
        return self.origin_y + self.length_y

    @property
    def top_z(self) -> float:
        """Nominal top surface Z coordinate (relative to spoilboard or machine WCS)."""
        return self.spoilboard_z + self.thickness_z

    @property
    def polygon(self) -> sg.Polygon | sg.MultiPolygon:
        """2D planar footprint of the raw stock material on the bed."""
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            return sg.Point(self.center_x, self.center_y).buffer(self.radius_r, quad_segs=16)

        if self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            return self.conforming_polygon

        return sg.box(self.x_min, self.y_min, self.x_max, self.y_max)

    def set_center(self, target_cx: float, target_cy: float):
        """Reposition raw stock so its XY center sits at (target_cx, target_cy)."""
        if self.shape in (StockShape.CYLINDER, StockShape.ROUND_PLATE):
            r = self.radius_r
            self.origin_x = target_cx - r
            self.origin_y = target_cy - r
        elif self.shape == StockShape.CONFORMING and self.conforming_polygon is not None:
            poly_cx = self.conforming_polygon.centroid.x
            poly_cy = self.conforming_polygon.centroid.y
            dx = target_cx - poly_cx
            dy = target_cy - poly_cy
            self.conforming_polygon = sa.translate(self.conforming_polygon, xoff=dx, yoff=dy)
            self.origin_x = float(self.conforming_polygon.bounds[0])
            self.origin_y = float(self.conforming_polygon.bounds[1])
        else:
            self.origin_x = target_cx - self.width_x / 2.0
            self.origin_y = target_cy - self.length_y / 2.0

    def contains_xy(self, x: float, y: float) -> bool:
        """Check if bed coordinate (x, y) is inside the stock material boundary."""
        return bool(self.polygon.contains(sg.Point(x, y)))

    def get_surface_z(self, x: float, y: float) -> float:
        """Get local top surface height from LiDAR heightmap if available, else nominal top."""
        if self.heightmap is not None:
            return self.heightmap.get_surface_z(x, y)
        return self.top_z
