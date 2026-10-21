"""
Material Database for CNC Subtractive Milling.

Provides physical, cutting, and tool-life parameters for workpiece materials:
- Wood / MDF / Plywood
- Engineering Plastics (Delrin / POM / Acrylic / HDPE)
- Non-Ferrous Metals (Aluminum 6061-T6 / 7075-T6 / Brass)
- Ferrous Metals (Mild Steel / Low Carbon Steel)
"""
from __future__ import annotations
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class WorkpieceMaterial:
    key: str
    name: str
    category: str
    density_g_cm3: float
    specific_cutting_force_kc_n_mm2: float  # Specific cutting force kc (N/mm^2)
    surface_speed_vc_min_m_min: float       # Min recommended surface speed Vc (m/min)
    surface_speed_vc_max_m_min: float       # Max recommended surface speed Vc (m/min)
    chipload_min_mm_tooth: float            # Min chipload fz (mm/tooth)
    chipload_max_mm_tooth: float            # Max chipload fz (mm/tooth)
    taylor_exponent_n: float                # Taylor tool-life exponent n
    taylor_constant_c: float                # Taylor constant C
    spindle_power_factor: float             # Relative spindle power requirement multiplier
    thermal_sensitivity: str                # Thermal/melting risk description
    recommended_flutes: int                 # Flute count recommendation (1, 2, or 3)
    coolant_recommendation: str             # Air blast, mist, flood, or dry

    def to_dict(self) -> dict:
        return asdict(self)


MATERIALS: dict[str, WorkpieceMaterial] = {
    "wood_mdf": WorkpieceMaterial(
        key="wood_mdf",
        name="Wood / MDF / Plywood",
        category="Organic / Composite",
        density_g_cm3=0.75,
        specific_cutting_force_kc_n_mm2=350.0,
        surface_speed_vc_min_m_min=250.0,
        surface_speed_vc_max_m_min=550.0,
        chipload_min_mm_tooth=0.040,
        chipload_max_mm_tooth=0.120,
        taylor_exponent_n=0.45,
        taylor_constant_c=1200.0,
        spindle_power_factor=0.6,
        thermal_sensitivity="Low. High feed prevents scorching / burn marks.",
        recommended_flutes=2,
        coolant_recommendation="Dry with active dust extraction"
    ),
    "plastic_delrin": WorkpieceMaterial(
        key="plastic_delrin",
        name="Engineering Plastic (Delrin / Acrylic)",
        category="Polymer",
        density_g_cm3=1.41,
        specific_cutting_force_kc_n_mm2=450.0,
        surface_speed_vc_min_m_min=180.0,
        surface_speed_vc_max_m_min=360.0,
        chipload_min_mm_tooth=0.035,
        chipload_max_mm_tooth=0.090,
        taylor_exponent_n=0.40,
        taylor_constant_c=850.0,
        spindle_power_factor=0.75,
        thermal_sensitivity="High melting risk. Requires crisp shearing chipload to evacuate heat.",
        recommended_flutes=1,
        coolant_recommendation="Cold air blast or dry"
    ),
    "aluminum_6061": WorkpieceMaterial(
        key="aluminum_6061",
        name="Aluminum (6061-T6 / 7075)",
        category="Non-Ferrous Alloy",
        density_g_cm3=2.70,
        specific_cutting_force_kc_n_mm2=850.0,
        surface_speed_vc_min_m_min=90.0,
        surface_speed_vc_max_m_min=220.0,
        chipload_min_mm_tooth=0.020,
        chipload_max_mm_tooth=0.055,
        taylor_exponent_n=0.35,
        taylor_constant_c=480.0,
        spindle_power_factor=1.2,
        thermal_sensitivity="Moderate. Built-up edge (BUE) risk; chip evacuation critical.",
        recommended_flutes=2,
        coolant_recommendation="Alcohol mist, WD-40, or air blast"
    ),
    "steel_mild": WorkpieceMaterial(
        key="steel_mild",
        name="Steel / Soft Carbon / Brass",
        category="Ferrous Alloy",
        density_g_cm3=7.85,
        specific_cutting_force_kc_n_mm2=1950.0,
        surface_speed_vc_min_m_min=35.0,
        surface_speed_vc_max_m_min=80.0,
        chipload_min_mm_tooth=0.012,
        chipload_max_mm_tooth=0.030,
        taylor_exponent_n=0.25,
        taylor_constant_c=220.0,
        spindle_power_factor=2.4,
        thermal_sensitivity="High cutting force and tool deflection. Strict DOC limits on hobby mills.",
        recommended_flutes=3,
        coolant_recommendation="Flood coolant or heavy mist"
    ),
}


def get_material(key: str) -> WorkpieceMaterial:
    return MATERIALS.get(key, MATERIALS["wood_mdf"])
