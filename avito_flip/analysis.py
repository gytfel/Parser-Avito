"""Разбор объявления: характеристики + дефекты."""

from __future__ import annotations

from dataclasses import dataclass

from .defects import DefectReport, analyze_defects
from .models import Listing
from .specs import Specs, extract_specs

_CONDITION_KEYS = ("состояние", "condition")


@dataclass
class Item:
    listing: Listing
    specs: Specs
    defects: DefectReport

    @property
    def usable_for_market(self) -> bool:
        """Годится как аналог для оценки рынка: не подделка, не на запчасти, без серьёзных дефектов и рисков.

        Объявления с предоплатой и подобными рисками — часто приманка мошенников с заниженной ценой.
        """
        d = self.defects
        return not (d.is_fake or d.is_parts or d.is_locked or d.risks) and d.level <= 1


def condition_of(listing: Listing) -> str | None:
    for k, v in listing.params.items():
        if k.strip().lower() in _CONDITION_KEYS:
            return v
    return None


def analyze(listing: Listing, profile: str = "generic") -> Item:
    specs = extract_specs(listing.title, listing.description, listing.params, profile)
    defects = analyze_defects(listing.full_text, specs.profile, condition_of(listing))
    return Item(listing, specs, defects)
