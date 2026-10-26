"""Сбор объявлений со всех площадок, оценка рынка и поиск выгодных лотов."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .analysis import Item, analyze
from .config import Category, ConfigError, Settings
from .fetcher import BlockedError, Fetcher, FetchError
from .models import Listing
from .pricing import Deal, evaluate, find_market, market_overview
from .sources import SOURCE_TITLES, AvitoSource, CsvSource, Source, YoulaSource
from .text import query_matches

_PROFILE_CATEGORY = {
    "phones": "phones", "tablets": "tablets", "laptops": "laptops", "gpu": "gpu",
    "consoles": "consoles", "audio": "audio", "watches": "watches",
}


@dataclass
class ScanResult:
    category: Category
    query: str
    deals: list[Deal]
    pool: list[Item]
    overview: list[dict]
    excluded: int = 0
    warnings: list[str] = field(default_factory=list)
    started: datetime = field(default_factory=datetime.now)
    by_source: dict[str, int] = field(default_factory=dict)

    @property
    def good(self) -> list[Deal]:
        return [d for d in self.deals if d.verdict in ("good", "risky")]


class Scanner:
    def __init__(self, settings: Settings, sources: dict[str, Source] | None = None,
                 fetcher: Fetcher | None = None, log=None, use_cache: bool = True):
        self.settings = settings
        self.log = log or (lambda msg: None)
        self.fetcher = fetcher
        if sources is None:
            self.fetcher = fetcher or Fetcher(settings.network, use_cache=use_cache, log=self.log)
            sources = self._build_sources()
        self.sources = sources

    def _build_sources(self) -> dict[str, Source]:
        s = self.settings
        out: dict[str, Source] = {}
        if s.source("avito").enabled:
            out["avito"] = AvitoSource(self.fetcher, s.region, s.sort, log=self.log)
        youla = s.source("youla")
        if youla.enabled:
            out["youla"] = YoulaSource(self.fetcher, youla.city or s.region, youla.price_divisor, log=self.log)
        csv_cfg = s.source("csv")
        if csv_cfg.enabled and csv_cfg.files:
            missing = [f for f in csv_cfg.files if not Path(f).is_file()]
            if missing:
                raise ConfigError(f"CSV-файл не найден: {', '.join(missing)}")
            out["csv"] = CsvSource(csv_cfg.files)
        return out

    def close(self) -> None:
        if self.fetcher:
            self.fetcher.close()

    # ------------------------------------------------------------------ сбор

    def _collect(self, category: Category, query: str, warnings: list[str]) -> tuple[dict[str, Listing], set[str]]:
        s = self.settings
        listings: dict[str, Listing] = {}
        buyable: set[str] = set()

        def add(found: list[Listing], buy: bool) -> None:
            for x in found:
                listings.setdefault(x.uid, x)
                if buy or x.source in s.buy_sources:
                    buyable.add(x.uid)

        for name, src in self.sources.items():
            title = getattr(src, "title", name)
            try:
                if name == "avito":
                    add(src.search(query, path=category.avito_path, region=s.region, pages=s.pages),
                        "avito" in s.buy_sources)
                    for region in s.market_regions:
                        if region != s.region:
                            add(src.search(query, path=category.avito_path, region=region, pages=s.market_pages), False)
                else:
                    pages = s.source(name).pages or 1
                    add(src.search(query, pages=pages), name in s.buy_sources)
            except BlockedError as e:
                warnings.append(f"{title}: доступ ограничен. {e}")
            except FetchError as e:
                warnings.append(f"{title}: {e}")
            except Exception as e:  # чужая вёрстка поменялась — не роняем весь поиск
                warnings.append(f"{title}: ошибка разбора ({e.__class__.__name__}: {e})")
        return listings, buyable

    # ------------------------------------------------------------------ оценка

    def scan(self, category: Category, query: str, *, price_min: int | None = None, price_max: int | None = None,
             details: int | None = None, extra: list[Listing] | None = None) -> ScanResult:
        s = self.settings
        warnings: list[str] = []
        started = datetime.now()
        listings, buyable = self._collect(category, query, warnings)
        for x in extra or []:
            listings[x.uid] = x
            buyable.add(x.uid)
        if not listings and warnings:
            raise FetchError("Не удалось получить объявления:\n" + "\n".join(warnings))

        excluded = 0
        items: dict[str, Item] = {}
        extra_uids = {x.uid for x in extra or []}
        for uid, x in listings.items():
            if uid not in extra_uids:
                if query and not query_matches(query, x.title):
                    continue
                if category.excluded(x.title, s.global_exclude):
                    excluded += 1
                    continue
            items[uid] = analyze(x, category.profile)

        lo = price_min if price_min is not None else category.min_price
        hi = price_max if price_max is not None else category.max_price
        candidates = [uid for uid in items if uid in buyable and (uid in extra_uids or (
            (lo is None or items[uid].listing.price >= lo) and (hi is None or items[uid].listing.price <= hi)))]

        deals = self._evaluate(category, items, candidates)

        n_details = s.details_top if details is None else details
        if n_details > 0:
            top = [d for d in sorted(deals, key=lambda d: d.score, reverse=True)
                   if d.verdict in ("good", "risky", "maybe") and not d.item.listing.detailed][:n_details]
            for i, d in enumerate(top, 1):
                x = d.item.listing
                src = self.sources.get(x.source)
                if src is None:
                    continue
                self.log(f"Открываю объявление {i}/{len(top)}: {x.title}")
                try:
                    src.fetch_details(x)
                except BlockedError as e:
                    warnings.append(f"Детали объявлений: доступ ограничен, остальные не загружены. {e}")
                    break
                except FetchError as e:
                    warnings.append(f"Не удалось открыть {x.url}: {e}")
                    continue
                items[x.uid] = analyze(x, category.profile)
            deals = self._evaluate(category, items, candidates)

        deals.sort(key=lambda d: d.score, reverse=True)
        by_source: dict[str, int] = {}
        for it in items.values():
            by_source[it.listing.source] = by_source.get(it.listing.source, 0) + 1
        return ScanResult(
            category=category, query=query, deals=deals, pool=list(items.values()),
            overview=market_overview(list(items.values())), excluded=excluded, warnings=warnings,
            started=started, by_source=by_source,
        )

    def _evaluate(self, category: Category, items: dict[str, Item], candidates: list[str]) -> list[Deal]:
        econ = self.settings.economics
        pool = list(items.values())
        threshold = category.similarity_threshold or econ.similarity_threshold
        return [evaluate(items[uid], find_market(items[uid], pool, econ, threshold), econ) for uid in candidates]

    # ------------------------------------------------------------------ одно объявление

    def analyze_url(self, url: str, category: Category | None = None, query: str | None = None,
                    details: int = 0) -> tuple[Deal, ScanResult]:
        src = self.sources.get("avito")
        if src is None:
            raise FetchError("Источник Авито отключён в настройках")
        m = re.search(r"_(\d{6,})(?:[/?#]|$)", url)
        listing = Listing(source="avito", id=m.group(1) if m else url, title="", price=0, url=url)
        src.fetch_details(listing)
        if not listing.title or not listing.price:
            raise FetchError("Не удалось прочитать заголовок или цену объявления — проверьте ссылку")
        profile = category.profile if category else "generic"
        item = analyze(listing, profile)
        if category is None:
            key = _PROFILE_CATEGORY.get(item.specs.profile, "any")
            category = self.settings.categories.get(key) or self.settings.category("any")
            item = analyze(listing, category.profile)
        if not query:
            query = item.specs.model or " ".join(listing.title.split()[:3])
        result = self.scan(category, query, details=details, extra=[listing])
        deal = next(d for d in result.deals if d.item.listing.uid == listing.uid)
        return deal, result


def source_title(name: str) -> str:
    return SOURCE_TITLES.get(name, name)
