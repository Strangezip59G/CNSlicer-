from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class BedLayoutType(str, Enum):
    ALUMINUM_TSLOT = "aluminum_tslot"                 # Parallel extruded T-tracks / clamping channels
    MDF_HYBRID_SPOILBOARD = "mdf_hybrid_spoilboard"   # MDF slats with integrated T-tracks & hold-down dogholes
    PRECISION_TOOLING_PLATE = "precision_tooling"     # Milled aluminum fixture plate with M6 threaded grid
    WASTEBOARD_RULER_GRID = "wasteboard_ruler_grid"   # Screen-printed metric wasteboard with alignment scales
    CUSTOM_BED = "custom_bed"                         # User-defined spoilboard


@dataclass
class CNCProfile:
    """Represents a physical CNC milling machine / router profile, analogous to 3D printer profiles."""
    id: str
    name: str
    manufacturer: str
    category: str
    bed_width_x: float          # Travel along X in mm
    bed_length_y: float         # Travel along Y in mm
    max_z_travel: float         # Maximum Z travel / gantry clearance in mm
    bed_layout: BedLayoutType
    spindle_type: str
    spindle_max_rpm: int
    spindle_min_rpm: int
    default_feed_xy: float      # Recommended default cutting feed in mm/min
    default_plunge_z: float     # Recommended default plunge feed in mm/min
    rapid_speed: float          # Max rapid travel speed in mm/min
    controller_dialect: str     # "grbl", "linuxcnc", "carbide", "bantam"
    t_slot_pitch_mm: float = 30.0  # Spacing between T-tracks or fixture dogholes
    has_enclosure: bool = False
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "category": self.category,
            "bed_width_x": self.bed_width_x,
            "bed_length_y": self.bed_length_y,
            "max_z_travel": self.max_z_travel,
            "bed_layout": self.bed_layout.value,
            "spindle_type": self.spindle_type,
            "spindle_max_rpm": self.spindle_max_rpm,
            "spindle_min_rpm": self.spindle_min_rpm,
            "default_feed_xy": self.default_feed_xy,
            "default_plunge_z": self.default_plunge_z,
            "rapid_speed": self.rapid_speed,
            "controller_dialect": self.controller_dialect,
            "t_slot_pitch_mm": self.t_slot_pitch_mm,
            "has_enclosure": self.has_enclosure,
            "description": self.description,
            "envelope_label": f"{self.bed_width_x:.0f} × {self.bed_length_y:.0f} × {self.max_z_travel:.0f} mm"
        }


# Standard Presets for the most popular Desktop and Workshop CNC Milling Machines
STANDARD_CNC_MACHINES: dict[str, CNCProfile] = {
    "genmitsu_3018": CNCProfile(
        id="genmitsu_3018",
        name="Genmitsu 3018-PROVer",
        manufacturer="SainSmart",
        category="Desktop CNC Mill",
        bed_width_x=300.0,
        bed_length_y=180.0,
        max_z_travel=45.0,
        bed_layout=BedLayoutType.ALUMINUM_TSLOT,
        spindle_type="775 High-Speed Motor (ER11)",
        spindle_max_rpm=10000,
        spindle_min_rpm=1000,
        default_feed_xy=800.0,
        default_plunge_z=200.0,
        rapid_speed=1500.0,
        controller_dialect="grbl",
        t_slot_pitch_mm=25.0,
        has_enclosure=False,
        description="The gold-standard entry-level desktop CNC router with extruded aluminum T-slot bed for PCBs, acrylic, and wood."
    ),
    "genmitsu_4030": CNCProfile(
        id="genmitsu_4030",
        name="Genmitsu PROVerXL 4030",
        manufacturer="SainSmart",
        category="Benchtop Heavy Mill",
        bed_width_x=400.0,
        bed_length_y=300.0,
        max_z_travel=110.0,
        bed_layout=BedLayoutType.ALUMINUM_TSLOT,
        spindle_type="Trim Router / 65mm Spindle",
        spindle_max_rpm=24000,
        spindle_min_rpm=8000,
        default_feed_xy=1500.0,
        default_plunge_z=350.0,
        rapid_speed=3000.0,
        controller_dialect="grbl",
        t_slot_pitch_mm=35.0,
        has_enclosure=False,
        description="Rigid C-beam benchtop CNC router with large aluminum T-track bed for non-ferrous metals and hardwoods."
    ),
    "shapeoko_4": CNCProfile(
        id="shapeoko_4",
        name="Shapeoko 4 Standard",
        manufacturer="Carbide 3D",
        category="Production CNC Router",
        bed_width_x=444.0,
        bed_length_y=444.0,
        max_z_travel=100.0,
        bed_layout=BedLayoutType.MDF_HYBRID_SPOILBOARD,
        spindle_type="Carbide Compact Router",
        spindle_max_rpm=24000,
        spindle_min_rpm=10000,
        default_feed_xy=1800.0,
        default_plunge_z=400.0,
        rapid_speed=4000.0,
        controller_dialect="grbl",
        t_slot_pitch_mm=50.0,
        has_enclosure=False,
        description="Heavy-duty hybrid T-slot and MDF spoilboard router with high-torque lead screws for heavy wood and aluminum machining."
    ),
    "nomad_3": CNCProfile(
        id="nomad_3",
        name="Carbide Nomad 3",
        manufacturer="Carbide 3D",
        category="Enclosed Precision Mill",
        bed_width_x=200.0,
        bed_length_y=200.0,
        max_z_travel=76.0,
        bed_layout=BedLayoutType.PRECISION_TOOLING_PLATE,
        spindle_type="High-Precision Brushless (ER11)",
        spindle_max_rpm=24000,
        spindle_min_rpm=9000,
        default_feed_xy=1200.0,
        default_plunge_z=300.0,
        rapid_speed=3000.0,
        controller_dialect="carbide",
        t_slot_pitch_mm=25.0,
        has_enclosure=True,
        description="Fully enclosed desktop micromill with precision tooling fixture bed and integrated probe for ultra-tight tolerance metal parts."
    ),
    "bantam_desktop": CNCProfile(
        id="bantam_desktop",
        name="Bantam Tools Desktop CNC",
        manufacturer="Bantam Tools",
        category="PCB & Precision Mill",
        bed_width_x=178.0,
        bed_length_y=127.0,
        max_z_travel=89.0,
        bed_layout=BedLayoutType.PRECISION_TOOLING_PLATE,
        spindle_type="High-Speed Spindle (ER11)",
        spindle_max_rpm=28000,
        spindle_min_rpm=10000,
        default_feed_xy=1200.0,
        default_plunge_z=300.0,
        rapid_speed=2500.0,
        controller_dialect="bantam",
        t_slot_pitch_mm=25.0,
        has_enclosure=True,
        description="Precision enclosed machine for clean rapid prototyping of aluminum enclosures and fine-pitch double-sided PCBs."
    ),
    "x_carve_1000": CNCProfile(
        id="x_carve_1000",
        name="Inventables X-Carve 1000mm",
        manufacturer="Inventables",
        category="Maker CNC Router",
        bed_width_x=750.0,
        bed_length_y=750.0,
        max_z_travel=65.0,
        bed_layout=BedLayoutType.WASTEBOARD_RULER_GRID,
        spindle_type="DeWalt 611 Router",
        spindle_max_rpm=27000,
        spindle_min_rpm=16000,
        default_feed_xy=2000.0,
        default_plunge_z=300.0,
        rapid_speed=4000.0,
        controller_dialect="grbl",
        t_slot_pitch_mm=50.0,
        has_enclosure=False,
        description="Large-format maker CNC with silk-screened wasteboard and metric alignment rulers for full-sheet furniture and signs."
    ),
    "carvera_atc": CNCProfile(
        id="carvera_atc",
        name="Makera Carvera (ATC)",
        manufacturer="Makera",
        category="Smart Desktop Mill",
        bed_width_x=360.0,
        bed_length_y=240.0,
        max_z_travel=140.0,
        bed_layout=BedLayoutType.PRECISION_TOOLING_PLATE,
        spindle_type="200W ATC Brushless Spindle",
        spindle_max_rpm=20000,
        spindle_min_rpm=8000,
        default_feed_xy=1500.0,
        default_plunge_z=350.0,
        rapid_speed=3500.0,
        controller_dialect="linuxcnc",
        t_slot_pitch_mm=30.0,
        has_enclosure=True,
        description="Smart enclosed desktop CNC featuring automated tool changer (ATC), wireless auto-probing, and vacuum table."
    ),
    "custom_mill": CNCProfile(
        id="custom_mill",
        name="Custom CNC Milling Machine",
        manufacturer="User Custom",
        category="Custom Spoilboard",
        bed_width_x=400.0,
        bed_length_y=400.0,
        max_z_travel=100.0,
        bed_layout=BedLayoutType.CUSTOM_BED,
        spindle_type="Custom Router / Spindle",
        spindle_max_rpm=16000,
        spindle_min_rpm=1000,
        default_feed_xy=1200.0,
        default_plunge_z=300.0,
        rapid_speed=3000.0,
        controller_dialect="grbl",
        t_slot_pitch_mm=40.0,
        has_enclosure=False,
        description="User-configured machine envelope and custom spoilboard bed dimensions."
    )
}


def get_machine_profile(machine_id: str) -> CNCProfile:
    """Retrieve CNC profile by identifier, defaulting to Genmitsu 3018."""
    return STANDARD_CNC_MACHINES.get(machine_id, STANDARD_CNC_MACHINES["genmitsu_3018"])
