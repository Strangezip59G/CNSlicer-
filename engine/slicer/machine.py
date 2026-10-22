from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class BedLayoutType(str, Enum):
    ALUMINUM_TSLOT = "aluminum_tslot"                 # Parallel extruded T-tracks / clamping channels
    MDF_HYBRID_SPOILBOARD = "mdf_hybrid_spoilboard"   # MDF slats with integrated T-tracks & hold-down dogholes
    PRECISION_TOOLING_PLATE = "precision_tooling"     # Milled aluminum fixture plate with M6 threaded grid
    WASTEBOARD_RULER_GRID = "wasteboard_ruler_grid"   # Screen-printed metric wasteboard with alignment scales
    CAST_IRON_TSLOT = "cast_iron_tslot"               # Heavy industrial cast-iron machine table with ground T-slots
    VACUUM_CHUCK_BED = "vacuum_chuck_bed"             # High-speed industrial vacuum grid plate (Datron style)
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
    "tormach_1100m": CNCProfile(
        id="tormach_1100m",
        name="Tormach 1100M",
        manufacturer="Tormach",
        category="Industrial Prototyping Mill",
        bed_width_x=457.0,
        bed_length_y=279.0,
        max_z_travel=412.0,
        bed_layout=BedLayoutType.CAST_IRON_TSLOT,
        spindle_type="7,500 RPM Heavy-Torque Spindle (BT30)",
        spindle_max_rpm=7500,
        spindle_min_rpm=250,
        default_feed_xy=2200.0,
        default_plunge_z=600.0,
        rapid_speed=7600.0,
        controller_dialect="linuxcnc",
        t_slot_pitch_mm=65.0,
        has_enclosure=True,
        description="Cast-iron personal industrial CNC mill equipped with BT30 taper, power drawbar, and flood coolant for steel and titanium."
    ),
    "datron_neo": CNCProfile(
        id="datron_neo",
        name="Datron neo (High-Speed Mill)",
        manufacturer="DATRON AG",
        category="High-Speed Industrial Mill",
        bed_width_x=520.0,
        bed_length_y=420.0,
        max_z_travel=220.0,
        bed_layout=BedLayoutType.VACUUM_CHUCK_BED,
        spindle_type="40,000 RPM Liquid-Cooled HF Spindle (HSK-E25)",
        spindle_max_rpm=40000,
        spindle_min_rpm=4000,
        default_feed_xy=6000.0,
        default_plunge_z=2000.0,
        rapid_speed=28000.0,
        controller_dialect="fanuc",
        t_slot_pitch_mm=40.0,
        has_enclosure=True,
        description="German ultra-high-speed precision milling center with 40k RPM spindle, integrated vacuum table, and automated tool sensor."
    ),
    "brother_speedio": CNCProfile(
        id="brother_speedio",
        name="Brother SPEEDIO S500X2",
        manufacturer="Brother Industries",
        category="Ultra-High Speed Machining Center",
        bed_width_x=500.0,
        bed_length_y=400.0,
        max_z_travel=300.0,
        bed_layout=BedLayoutType.CAST_IRON_TSLOT,
        spindle_type="16,000 RPM High-Acceleration Spindle (BT30 Big-Plus)",
        spindle_max_rpm=16000,
        spindle_min_rpm=150,
        default_feed_xy=8000.0,
        default_plunge_z=3000.0,
        rapid_speed=50000.0,
        controller_dialect="fanuc",
        t_slot_pitch_mm=75.0,
        has_enclosure=True,
        description="Blazing fast production center with 50 m/min rapids, sub-second tool changes, and high-efficiency synchronization for batch parts."
    ),
    "haas_vf2": CNCProfile(
        id="haas_vf2",
        name="Haas VF-2 (Industrial VMC)",
        manufacturer="Haas Automation",
        category="Industrial Vertical Machining Center",
        bed_width_x=762.0,
        bed_length_y=406.0,
        max_z_travel=508.0,
        bed_layout=BedLayoutType.CAST_IRON_TSLOT,
        spindle_type="12,000 RPM Inline Direct-Drive (CT40 / BT40)",
        spindle_max_rpm=12000,
        spindle_min_rpm=500,
        default_feed_xy=4000.0,
        default_plunge_z=1200.0,
        rapid_speed=25400.0,
        controller_dialect="haas",
        t_slot_pitch_mm=80.0,
        has_enclosure=True,
        description="Industry benchmark 40-taper vertical machining center with high-rigidity cast-iron frame and full chip enclosure."
    ),
    "dmg_mori_cmx600v": CNCProfile(
        id="dmg_mori_cmx600v",
        name="DMG MORI CMX 600 V",
        manufacturer="DMG MORI",
        category="Production Vertical Machining Center",
        bed_width_x=600.0,
        bed_length_y=560.0,
        max_z_travel=510.0,
        bed_layout=BedLayoutType.CAST_IRON_TSLOT,
        spindle_type="12,000 RPM DirectDrive Spindle (SK40 / BT40)",
        spindle_max_rpm=12000,
        spindle_min_rpm=100,
        default_feed_xy=5000.0,
        default_plunge_z=1500.0,
        rapid_speed=30000.0,
        controller_dialect="siemens",
        t_slot_pitch_mm=90.0,
        has_enclosure=True,
        description="Heavy-duty Japanese-German industrial production machining center with high dynamic rigidity for mould and aerospace components."
    ),
    "avid_pro4848": CNCProfile(
        id="avid_pro4848",
        name="Avid CNC PRO4848 (4×4 ft)",
        manufacturer="Avid CNC",
        category="Industrial Gantry CNC Router",
        bed_width_x=1250.0,
        bed_length_y=1250.0,
        max_z_travel=200.0,
        bed_layout=BedLayoutType.MDF_HYBRID_SPOILBOARD,
        spindle_type="4.0 kW Air-Cooled Industrial Spindle (ER25)",
        spindle_max_rpm=24000,
        spindle_min_rpm=6000,
        default_feed_xy=6000.0,
        default_plunge_z=1200.0,
        rapid_speed=12000.0,
        controller_dialect="linuxcnc",
        t_slot_pitch_mm=100.0,
        has_enclosure=False,
        description="Large-format 4×4 ft industrial gantry CNC with precision rack-and-pinion drives for architectural millwork, wood, and aluminum."
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
