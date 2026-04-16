from dataclasses import dataclass
from enum import Enum


class EventCategory(str, Enum):
    earthquake = "earthquake"
    tsunami = "tsunami"
    volcano = "volcano"
    flood = "flood"
    landslide = "landslide"
    drought = "drought"
    cyclone = "cyclone"
    storm = "storm"
    extreme_heat = "extreme_heat"
    extreme_cold = "extreme_cold"
    snow = "snow"
    wind = "wind"
    wildfire = "wildfire"
    humanitarian = "humanitarian"


@dataclass(frozen=True)
class CategoryMeta:
    label: str
    icon_key: str
    color: str
    priority: int


CATEGORY_META: dict[EventCategory, CategoryMeta] = {
    EventCategory.earthquake: CategoryMeta("Terremoto", "earthquake", "#e74c3c", 1),
    EventCategory.tsunami: CategoryMeta("Tsunami", "tsunami", "#2980b9", 1),
    EventCategory.volcano: CategoryMeta("Vulcano", "volcano", "#e67e22", 2),
    EventCategory.flood: CategoryMeta("Alluvione", "flood", "#3498db", 2),
    EventCategory.landslide: CategoryMeta("Frana", "landslide", "#795548", 2),
    EventCategory.drought: CategoryMeta("Siccita", "drought", "#f39c12", 3),
    EventCategory.cyclone: CategoryMeta("Ciclone", "cyclone", "#9b59b6", 1),
    EventCategory.storm: CategoryMeta("Temporale", "storm", "#5d6d7e", 2),
    EventCategory.extreme_heat: CategoryMeta("Caldo estremo", "extreme_heat", "#e74c3c", 2),
    EventCategory.extreme_cold: CategoryMeta("Freddo estremo", "extreme_cold", "#85c1e9", 2),
    EventCategory.snow: CategoryMeta("Neve estrema", "snow", "#aed6f1", 3),
    EventCategory.wind: CategoryMeta("Vento forte", "wind", "#717d7e", 3),
    EventCategory.wildfire: CategoryMeta("Incendio", "wildfire", "#e74c3c", 2),
    EventCategory.humanitarian: CategoryMeta("Umanitario", "humanitarian", "#e74c3c", 3),
}