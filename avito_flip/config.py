"""Загрузка настроек: встроенный default_config.yaml + пользовательский config.yaml поверх."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .text import normalize

DEFAULT_CONFIG = Path(__file__).with_name("default_config.yaml")


class ConfigError(ValueError):
    """Ошибка в настройках или аргументах — показывается пользователю без трассировки."""


@dataclass
class Economics:
    sell_quantile: float = 0.4  # по какой точке рынка считаем продажу (0.5 — медиана)
    sell_haggle: float = 0.05  # сколько уступим покупателю
    buy_haggle: float = 0.05  # сколько рассчитываем выторговать у продавца, если торг уместен
    fixed_costs: float = 300  # дорога, время, упаковка, продвижение
    fee_percent: float = 0.0  # комиссия площадки/доставки с продажи (0.05 = 5%)
    min_profit: float = 2000
    min_roi: float = 0.10
    min_comparables: int = 4
    similarity_threshold: float = 0.7
    suspicious_ratio: float = 0.5  # дешевле этой доли медианы без причин — подозрительно


@dataclass
class Network:
    delay_min: float = 4.0
    delay_max: float = 9.0
    timeout: float = 30.0
    retries: int = 2
    proxy: str | None = None
    impersonate: bool = True  # curl_cffi с отпечатком Chrome, если установлен
    browser: bool = False  # загрузка страниц через Playwright (медленнее, но реже блокируют)
    headless: bool = False  # окно браузера видно — при проверке «я не робот» её можно пройти вручную
    browser_channel: str | None = None  # chrome, msedge или chromium; по умолчанию — первый найденный
    captcha_wait: float = 180  # сколько секунд ждать, пока проверку проходят в окне браузера
    cache_ttl_minutes: float = 60
    cache_dir: str = ".cache"
    cookies: str | None = None  # строка Cookie из браузера, если Авито просит капчу


@dataclass
class Category:
    key: str
    title: str
    profile: str = "generic"
    avito_path: str = ""
    queries: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    similarity_threshold: float | None = None
    min_price: int | None = None
    max_price: int | None = None

    def excluded(self, title: str, extra: list[str] = ()) -> str | None:
        """Шаблон исключения, которому соответствует заголовок (аксессуар, «куплю»...)."""
        t = normalize(title)
        for pattern in [*self.exclude, *extra]:
            if re.search(pattern, t):
                return pattern
        return None


@dataclass
class SourceConfig:
    enabled: bool = True
    city: str | None = None
    pages: int | None = None
    price_divisor: str | float = "auto"
    files: list[str] = field(default_factory=list)


@dataclass
class Settings:
    region: str = "moskva"
    market_regions: list[str] = field(default_factory=list)
    pages: int = 3
    market_pages: int = 2
    sort: str = "date"
    details_top: int = 8
    buy_sources: list[str] = field(default_factory=lambda: ["avito", "youla"])
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    global_exclude: list[str] = field(default_factory=list)
    economics: Economics = field(default_factory=Economics)
    network: Network = field(default_factory=Network)
    telegram: dict[str, str] = field(default_factory=dict)
    categories: dict[str, Category] = field(default_factory=dict)
    reports_dir: str = "reports"

    def category(self, key: str) -> Category:
        if key in self.categories:
            return self.categories[key]
        for c in self.categories.values():
            if c.title.lower() == key.lower():
                return c
        names = ", ".join(self.categories)
        raise ConfigError(f"Нет категории «{key}». Доступны: {names}")

    def source(self, name: str) -> SourceConfig:
        return self.sources.get(name) or SourceConfig(enabled=False)


def _build(cls, data: dict | None):
    data = data or {}
    names = {f.name for f in fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ConfigError(f"Неизвестные параметры в разделе {cls.__name__}: {', '.join(sorted(unknown))}")
    return cls(**data)


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_settings(path: str | Path | None = None) -> Settings:
    data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    user_path = Path(path) if path else Path("config.yaml")
    if user_path.exists():
        data = _merge(data, yaml.safe_load(user_path.read_text(encoding="utf-8")) or {})
    elif path:
        raise ConfigError(f"Файл настроек не найден: {path}")
    return settings_from_dict(data)


def settings_from_dict(data: dict) -> Settings:
    data = dict(data)
    categories = {
        key: _build(Category, {"key": key, **(value or {})}) for key, value in (data.pop("categories", {}) or {}).items()
    }
    sources = {name: _build(SourceConfig, value) for name, value in (data.pop("sources", {}) or {}).items()}
    economics = _build(Economics, data.pop("economics", {}))
    network = _build(Network, data.pop("network", {}))
    s = _build(Settings, data)
    s.categories, s.sources, s.economics, s.network = categories, sources, economics, network
    return s


def enable_browser_mode(path: str | Path = "config.yaml") -> Path:
    """Вписать network.browser: true в пользовательский config.yaml, сохранив остальное и комментарии."""
    p = Path(path)
    original = p.read_text(encoding="utf-8") if p.exists() else ""
    before = yaml.safe_load(original) or {} if original.strip() else {}
    if not isinstance(before, dict):
        raise ConfigError(f"{p}: ожидался словарь настроек")
    old_network = before.get("network") or {}
    if old_network.get("browser") is True:
        return p

    lines = original.splitlines()
    network_at = None
    done = False
    for i, line in enumerate(lines):
        if re.match(r"^network:\s*(#.*)?$", line):
            network_at = i
            continue
        if network_at is not None and re.match(r"^\S", line):
            break  # начался следующий раздел
        m = network_at is not None and re.match(r"^(\s+)browser:\s*[^#]*?(\s*#.*)?$", line)
        if m:
            lines[i] = f"{m.group(1)}browser: true{m.group(2) or ''}"
            done = True
            break
    if not done and network_at is not None:
        nxt = lines[network_at + 1] if network_at + 1 < len(lines) else ""
        indent = re.match(r"^(\s+)\S", nxt).group(1) if re.match(r"^(\s+)\S", nxt) else "  "
        lines.insert(network_at + 1, f"{indent}browser: true")
    elif not done:
        lines += ([""] if lines else ["# Свои настройки парсера; все параметры — в config.example.yaml"])
        lines += ["network:", "  browser: true"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Проверяем, что изменилось ровно одно значение; иначе возвращаем файл как был.
    after = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    expected = {**before, "network": {**old_network, "browser": True}}
    if after != expected:
        if original:
            p.write_text(original, encoding="utf-8")
        else:
            p.unlink()
        raise ConfigError(f"не получилось аккуратно изменить {p}")
    return p
