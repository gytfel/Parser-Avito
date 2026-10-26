"""Общие структуры данных: объявление с любой площадки."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """Одно объявление (Авито, Юла, CSV-выгрузка с другой площадки)."""

    source: str
    id: str
    title: str
    price: int
    url: str = ""
    description: str = ""
    location: str = ""
    published: datetime | None = None
    params: dict[str, str] = field(default_factory=dict)
    seller: str = ""
    images: int = 0
    # True, если описание и характеристики взяты со страницы объявления,
    # а не из короткого сниппета в выдаче.
    detailed: bool = False

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def full_text(self) -> str:
        parts = [self.title, self.description]
        parts += [f"{k}: {v}" for k, v in self.params.items()]
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Listing:
        d = dict(d)
        if d.get("published"):
            d["published"] = datetime.fromisoformat(d["published"])
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
