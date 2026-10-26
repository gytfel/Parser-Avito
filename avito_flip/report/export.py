"""Выгрузка результатов в CSV (для Excel) и JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..scanner import ScanResult, source_title

_COLUMNS = [
    "категория", "запрос", "вердикт", "название", "характеристики", "площадка", "город", "цена", "покупка_с_торгом",
    "продать_за", "прибыль", "roi", "аналогов", "медиана_рынка", "уверенность", "стратегия", "дефекты", "плюсы",
    "риски", "ссылка",
]


def _rows(results: list[ScanResult]):
    for r in results:
        for d in r.deals:
            x = d.item.listing
            yield {
                "категория": r.category.title,
                "запрос": r.query,
                "вердикт": d.verdict_label,
                "название": x.title,
                "характеристики": d.item.specs.describe(),
                "площадка": source_title(x.source),
                "город": x.location,
                "цена": x.price,
                "покупка_с_торгом": round(d.buy_price),
                "продать_за": round(d.sell_price) if d.sell_price else "",
                "прибыль": round(d.profit) if d.profit is not None else "",
                "roi": round(d.roi, 3) if d.roi is not None else "",
                "аналогов": d.market.n if d.market else 0,
                "медиана_рынка": round(d.market.median) if d.market else "",
                "уверенность": d.confidence,
                "стратегия": d.strategy,
                "дефекты": "; ".join(f.label for f in d.item.defects.defects),
                "плюсы": "; ".join(b.label for b in d.item.defects.bonuses),
                "риски": "; ".join(d.risks),
                "ссылка": x.url,
            }


def save_csv(results: list[ScanResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig и «;» — чтобы Excel сразу открыл кириллицу и столбцы.
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(_rows(results))
    return path


def save_json(results: list[ScanResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for r in results:
        deals = []
        for d in r.deals:
            deals.append({
                "listing": d.item.listing.to_dict(),
                "model_key": d.item.specs.key,
                "specs": d.item.specs.describe(),
                "verdict": d.verdict,
                "buy_price": d.buy_price,
                "sell_price": d.sell_price,
                "profit": d.profit,
                "roi": d.roi,
                "confidence": d.confidence,
                "strategy": d.strategy,
                "defects": [vars(f) for f in d.item.defects.defects],
                "bonuses": [vars(b) for b in d.item.defects.bonuses],
                "risks": d.risks,
                "notes": d.notes,
                "comparables": [
                    {"title": c.item.listing.title, "price": c.price, "url": c.item.listing.url,
                     "source": c.item.listing.source, "similarity": round(c.similarity, 3)}
                    for c in (d.market.comparables if d.market else [])
                ],
            })
        data.append({"category": r.category.key, "query": r.query, "started": r.started.isoformat(),
                     "overview": r.overview, "warnings": r.warnings, "deals": deals})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
