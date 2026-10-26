"""Извлечение характеристик товара (модель, память, АКБ, процессор...) из текста.

Модель ищется только в заголовке и параметрах объявления: в описании часто
упоминают другие модели («подойдёт чехол от 14-го»), это сбивало бы группировку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from .text import jaccard, normalize, tokens

PROFILES = ("phones", "tablets", "laptops", "gpu", "consoles", "audio", "watches", "generic")

# Насколько сильно расхождение атрибута снижает похожесть двух объявлений.
_ATTR_PENALTY = {
    "chip": 0.4,
    "cpu": 0.5,
    "gpu": 0.5,
    "vram": 0.5,
    "edition": 0.6,
    "gen": 0.4,
    "size": 0.7,
}
_MISSING_PENALTY = 0.92
_VALID_STORAGE = {16, 32, 64, 128, 256, 512, 1024, 2048, 4096}


@dataclass
class Specs:
    profile: str
    brand: str | None = None
    model: str | None = None
    storage_gb: int | None = None
    ram_gb: int | None = None
    battery: int | None = None  # здоровье аккумулятора, %
    # Атрибуты, которые входят в ключ группировки (процессор, видеокарта, издание...).
    attrs: dict[str, str] = field(default_factory=dict)
    tokens: frozenset[str] = frozenset()

    @property
    def key(self) -> str:
        """Ключ группы сравнимых товаров, например «iphone 13 pro | 128gb»."""
        parts = [self.model or "~" + " ".join(sorted(self.tokens)[:6])]
        if self.storage_gb:
            parts.append(_fmt_storage(self.storage_gb))
        if self.ram_gb:
            parts.append(f"{self.ram_gb}gb ram")
        parts += [str(self.attrs[k]) for k in sorted(self.attrs)]
        return " | ".join(parts)

    def describe(self, with_battery: bool = True) -> str:
        parts = []
        if self.model:
            name = pretty_model(self.model)
            if self.brand and not name.lower().startswith(self.brand.lower()):
                name = f"{self.brand} {name}"
            parts.append(name)
        if self.storage_gb:
            parts.append(_fmt_storage(self.storage_gb).upper().replace("GB", " ГБ").replace("TB", " ТБ"))
        if self.ram_gb:
            parts.append(f"ОЗУ {self.ram_gb} ГБ")
        parts += [_fmt_attr(k, v) for k, v in sorted(self.attrs.items())]
        if self.battery and with_battery:
            parts.append(f"АКБ {self.battery}%")
        return " · ".join(parts) if parts else "модель не распознана"

    def similarity(self, other: Specs) -> float:
        """0..1 — насколько объявление годится как аналог для сравнения цены."""
        if self.model and other.model:
            if self.model != other.model:
                return 0.0
            score = 1.0
        else:
            score = min(0.9, jaccard(self.tokens, other.tokens) * 1.15)
        for a, b in ((self.storage_gb, other.storage_gb), (self.ram_gb, other.ram_gb)):
            if a and b and a != b:
                score *= 0.55
            elif bool(a) != bool(b):
                score *= _MISSING_PENALTY
        for k in set(self.attrs) | set(other.attrs):
            a, b = self.attrs.get(k), other.attrs.get(k)
            if a and b and a != b:
                score *= _ATTR_PENALTY.get(k, 0.6)
            elif not a or not b:
                score *= _MISSING_PENALTY
        return score


_EDITION = {"digital": "цифровая (без дисковода)", "disc": "с дисководом"}


def _fmt_attr(key: str, value: str) -> str:
    if key == "edition":
        return _EDITION.get(value, value)
    if key == "gen":
        return f"{value}-е поколение" if len(value) == 1 else value
    if key == "vram":
        return f"{value.upper().replace('GB', '')} ГБ видеопамяти"
    return pretty_model(value) if key in {"gpu", "chip"} else value.upper() if key == "cpu" else value


_PRETTY = {
    "iphone": "iPhone", "ipad": "iPad", "macbook": "MacBook", "airpods": "AirPods", "xbox": "Xbox",
    "poco": "POCO", "rtx": "RTX", "gtx": "GTX", "rx": "RX", "ti": "Ti", "xt": "XT", "xtx": "XTX",
    "oled": "OLED", "lcd": "LCD", "se": "SE", "fe": "FE", "mini": "mini", "hp": "HP", "msi": "MSI",
    "tuf": "TUF", "rog": "ROG", "xps": "XPS", "loq": "LOQ", "jbl": "JBL", "gre": "GRE",
}


def pretty_model(model: str) -> str:
    """«galaxy s23 ultra» -> «Galaxy S23 Ultra», «ps5 slim» -> «PlayStation 5 Slim»."""
    words = []
    for w in model.split():
        if w in _PRETTY:
            words.append(_PRETTY[w])
        elif m := re.fullmatch(r"ps(\d)", w):
            words.append(f"PlayStation {m.group(1)}")
        elif re.fullmatch(r"[a-z]{1,2}\d+[a-z]*", w):
            words.append(w.upper() if len(w) <= 4 else w[0].upper() + w[1:])
        else:
            words.append(w[:1].upper() + w[1:])
    return " ".join(words)


def _fmt_storage(gb: int) -> str:
    return f"{gb // 1024}tb" if gb >= 1024 and gb % 1024 == 0 else f"{gb}gb"


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --------------------------------------------------------------------------- модели

def _phone_model(t: str) -> tuple[str, str, dict] | None:
    m = re.search(
        r"\biphone\s*(1[0-9]|[5-9]|xs|xr|x|se)(?![0-9])\s*(e\b)?\s*(pro\s*max|pro|max|plus|mini|air)?\b", t
    )
    if m:
        num, e, var = m.group(1), m.group(2) or "", _clean(m.group(3)).replace("promax", "pro max")
        attrs = {}
        if num == "se":
            g = re.search(r"\bse\s*\(?\s*(2016|2020|2022|[123])\b", t)
            if g:
                attrs["gen"] = {"2016": "1", "2020": "2", "2022": "3"}.get(g.group(1), g.group(1))
        return "Apple", _clean(f"iphone {num}{e} {var}"), attrs

    m = re.search(
        r"\b(?:samsung|galaxy)\s+(?:galaxy\s+)?(z\s*fold|z\s*flip|fold|flip|note|s|a|m)\s*(\d{1,2})(?![0-9])"
        r"\s*(ultra|plus|fe|lite|\+)?",
        t,
    )
    if m:
        series = _clean(m.group(1)).replace("z fold", "z fold ").replace("z flip", "z flip ")
        suffix = {"+": "plus"}.get(m.group(3) or "", m.group(3) or "")
        return "Samsung", _clean(f"galaxy {series}{m.group(2)} {suffix}"), {}

    m = re.search(
        r"\b(redmi\s+note|redmi|poco|xiaomi\s+mi|xiaomi|mi)\s+([a-z]?\d{1,2}[a-z]?)(?![0-9])"
        r"\s*(pro\s*plus|pro\+|pro|ultra|lite|t\b|s\b|c\b)?",
        t,
    )
    if m:
        series = {"xiaomi mi": "mi"}.get(m.group(1), _clean(m.group(1)))
        suffix = (m.group(3) or "").replace("pro+", "pro plus").replace("proplus", "pro plus")
        return "Xiaomi", _clean(f"{series} {m.group(2)} {suffix}"), {}

    m = re.search(r"\bpixel\s+(\d{1,2}a?)(?![0-9])\s*(pro\s*xl|pro\s*fold|pro|xl|fold)?", t)
    if m:
        return "Google", _clean(f"pixel {m.group(1)} {m.group(2) or ''}"), {}

    m = re.search(
        r"\b(huawei|honor)\s+(?:(p|mate|nova|magic|pura|x)\s*)?(\d{1,2}[a-z]?)(?![0-9])\s*(pro\+?|lite|ultra|plus|se)?",
        t,
    )
    if m:
        return m.group(1).title(), _clean(f"{m.group(1)} {m.group(2) or ''}{m.group(3)} {m.group(4) or ''}"), {}

    m = re.search(
        r"\b(realme|oneplus|nothing\s+phone|tecno|infinix|vivo|oppo|nokia|motorola|moto|zte|xperia|rog\s+phone)"
        r"\s+(\(?[a-z]{0,6}\s?\d{1,3}[a-z]{0,3}\)?)(?![0-9])\s*(pro\+?|ultra|plus|lite|neo)?",
        t,
    )
    if m:
        brand = _clean(m.group(1)).title()
        return brand, _clean(f"{m.group(1)} {m.group(2).strip('()')} {m.group(3) or ''}"), {}
    return None


def _tablet_model(t: str) -> tuple[str, str, dict] | None:
    m = re.search(r"\bipad\s*(pro|air|mini)?\s*(\d{1,2}(?:[.,]\d)?)?(?![0-9])", t)
    if m:
        attrs = {}
        chip = re.search(r"\bm([1-5])\b", t)
        if chip:
            attrs["chip"] = f"m{chip.group(1)}"
        year = re.search(r"\b(20[12]\d)\b", t)
        if year and not chip:
            attrs["gen"] = year.group(1)
        num = (m.group(2) or "").replace(",", ".")
        return "Apple", _clean(f"ipad {m.group(1) or ''} {num}"), attrs
    m = re.search(r"\bgalaxy\s+tab\s+(s|a)\s*(\d{1,2})(?![0-9])\s*(fe|ultra|plus|lite|\+)?", t)
    if m:
        suffix = {"+": "plus"}.get(m.group(3) or "", m.group(3) or "")
        return "Samsung", _clean(f"galaxy tab {m.group(1)}{m.group(2)} {suffix}"), {}
    m = re.search(r"\b(xiaomi\s+pad|redmi\s+pad|mi\s+pad)\s*(\d{1,2})?\s*(pro|se|plus)?", t)
    if m:
        return "Xiaomi", _clean(f"{m.group(1).replace('mi pad', 'xiaomi pad')} {m.group(2) or ''} {m.group(3) or ''}"), {}
    return None


_LAPTOP_BRANDS = (
    r"asus|lenovo|hp|dell|acer|msi|huawei|honor|xiaomi|redmibook|gigabyte|samsung|thunderobot|machenike"
    r"|ardor|digma|chuwi|tecno|infinix|apple"
)
_LAPTOP_SERIES = (
    r"rog\s*strix|rog\s*zephyrus|rog\s*flow|rog|tuf|zenbook|vivobook|expertbook|legion|ideapad|thinkpad|thinkbook"
    r"|yoga|loq|pavilion|victus|omen|elitebook|probook|envy|spectre|xps|inspiron|latitude|vostro|alienware|aspire"
    r"|nitro|predator|swift|extensa|katana|cyborg|stealth|raider|pulse|thin|modern|prestige|bravo|matebook"
    r"|magicbook|aorus|aero|galaxy\s+book|g15|g16"
)


def _laptop_model(t: str) -> tuple[str, str, dict] | None:
    attrs = {}
    m = re.search(r"\bmacbook\s*(air|pro)?", t)
    if m:
        chip = re.search(r"\bm([1-5])\s*(pro|max|ultra)?\b", t)
        if chip:
            attrs["chip"] = _clean(f"m{chip.group(1)} {chip.group(2) or ''}")
        else:
            cpu = _laptop_cpu(t)
            if cpu:
                attrs["cpu"] = cpu
        size = re.search(r"\b(13|14|15|16)(?:[.,]\d)?\s*(?:inch|дюйм\w*)", t) or re.search(
            r"\bmacbook\s*(?:air|pro)?\s*(13|14|15|16)\b", t
        )
        if size:
            attrs["size"] = size.group(1) + '"'
        return "Apple", _clean(f"macbook {m.group(1) or ''}"), attrs

    cpu, gpu = _laptop_cpu(t), _laptop_gpu(t)
    if cpu:
        attrs["cpu"] = cpu
    if gpu:
        attrs["gpu"] = gpu
    m = re.search(rf"\b({_LAPTOP_BRANDS})\s+(?:gaming\s+)?({_LAPTOP_SERIES})(?:\s+gaming)?(?:\s*([a-z]{{0,2}}\d{{1,4}}[a-z]{{0,2}})(?![0-9]))?", t)
    if m:
        brand = m.group(1).upper() if m.group(1) in {"hp", "msi"} else m.group(1).title()
        return brand, _clean(f"{m.group(1)} {m.group(2)} {m.group(3) or ''}"), attrs
    if attrs:
        brand = re.search(rf"\b({_LAPTOP_BRANDS})\b", t)
        return (brand.group(1).title() if brand else None), None, attrs
    return None


def _laptop_cpu(t: str) -> str | None:
    m = re.search(r"\b(i[3579])[\s-]*(\d{4,5}[a-z]{0,3})\b", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"\bryzen\s*([3579])\s*(\d{4}[a-z]{0,3})\b", t)
    if m:
        return f"ryzen {m.group(1)} {m.group(2)}"
    m = re.search(r"\bcore\s*ultra\s*([579])\s*(\d{3}[a-z]{0,2})\b", t)
    if m:
        return f"ultra {m.group(1)} {m.group(2)}"
    return None


def _laptop_gpu(t: str) -> str | None:
    m = re.search(r"\b(rtx|gtx)\s*(\d{3,4})\s*(ti)?\b", t)
    if m:
        return _clean(f"{m.group(1)} {m.group(2)} {m.group(3) or ''}")
    return None


_GPU_VENDORS = (
    r"asus|msi|gigabyte|palit|zotac|inno3d|kfa2|galax|colorful|sapphire|powercolor|xfx|asrock|gainward|pny"
    r"|evga|maxsun|afox|biostar|sinotex|ninja"
)


def _gpu_model(t: str) -> tuple[str, str, dict] | None:
    m = re.search(r"\b(rtx|gtx|rx|arc)\s*(a?\d{3,4})\s*(ti\s*super|ti|super|xtx|xt|gre)?\b", t)
    if not m:
        return None
    attrs = {}
    vram = re.search(r"\b(\d{1,2})\s*gb\s*(?:gddr\w*|vram|видеопамят\w*)", t) or re.search(r"\b(\d{1,2})\s*gb\b", t)
    if vram and 2 <= int(vram.group(1)) <= 32:
        attrs["vram"] = f"{vram.group(1)}gb"
    chip_maker = {"rtx": "NVIDIA", "gtx": "NVIDIA", "rx": "AMD", "arc": "Intel"}[m.group(1)]
    return chip_maker, _clean(f"{m.group(1)} {m.group(2)} {m.group(3) or ''}"), attrs


def _console_model(t: str) -> tuple[str, str, dict] | None:
    attrs = {}
    m = re.search(r"\bps([345])\b(?:\s*(slim|pro|fat))?", t)
    if m:
        variant = m.group(2) or ("slim" if re.search(r"\bslim\b", t) else "pro" if re.search(r"\bps\d\s+pro\b", t) else "")
        if m.group(1) == "5":
            if re.search(r"digital|диджитал|цифров\w*\s+(?:верси|издан)|без\s+(?:дисковод|привод)\w*", t):
                attrs["edition"] = "digital"
            elif re.search(r"(?:с|со)\s+(?:дисковод|привод)\w*|\bdisc\b|blu-?ray|дисков\w*\s+(?:верси|издан)\w*", t):
                attrs["edition"] = "disc"
        return "Sony", _clean(f"ps{m.group(1)} {variant if variant != 'fat' else ''}"), attrs
    m = re.search(r"\bxbox\s*(series\s*[xs]|one\s*[xs]?|360)?\b", t)
    if m and m.group(1):
        return "Microsoft", _clean(f"xbox {m.group(1)}"), attrs
    m = re.search(r"\b(?:nintendo\s+)?switch\b\s*(oled|lite|2)?", t)
    if m and ("nintendo" in t or m.group(1)):
        return "Nintendo", _clean(f"switch {m.group(1) or ''}"), attrs
    m = re.search(r"\bsteam\s*deck\b\s*(oled|lcd)?", t)
    if m:
        return "Valve", _clean(f"steam deck {m.group(1) or ''}"), attrs
    return None


def _audio_model(t: str) -> tuple[str, str, dict] | None:
    m = re.search(r"\bairpods\s*(pro|max)?\s*\(?\s*(\d)?(?![0-9])", t)
    if m:
        return "Apple", _clean(f"airpods {m.group(1) or ''} {m.group(2) or ''}"), {}
    m = re.search(r"\b(wh|wf)[\s-]*1000\s*x?m\s*(\d)\b", t)
    if m:
        return "Sony", f"sony {m.group(1)}-1000xm{m.group(2)}", {}
    m = re.search(r"\b(?:яндекс|yandex)?\s*станци\w*\s*(мини|лайт|макс|миди|стрит|дуо|2)?", t)
    if m and ("яндекс" in t or "yandex" in t or "алис" in t):
        variant = {"мини": "mini", "лайт": "lite", "макс": "max", "миди": "midi", "стрит": "street", "дуо": "duo"}
        return "Яндекс", _clean(f"yandex station {variant.get(m.group(1) or '', m.group(1) or '')}"), {}
    m = re.search(r"\b(jbl|marshall|bose|sennheiser)\s+([a-z]+\s?\d{0,3}[a-z]?)", t)
    if m:
        return m.group(1).upper() if m.group(1) == "jbl" else m.group(1).title(), _clean(f"{m.group(1)} {m.group(2)}"), {}
    return None


def _watch_model(t: str) -> tuple[str, str, dict] | None:
    attrs = {}
    size = re.search(r"\b(38|40|41|42|44|45|46|47|49)\s*mm\b", t)
    if size:
        attrs["size"] = f"{size.group(1)}mm"
    m = re.search(r"\bapple\s*watch\s*(?:series\s*)?(ultra\s*\d?|se\s*\d?|s?\d{1,2})?(?![0-9])", t)
    if m:
        v = _clean(m.group(1) or "")
        v = re.sub(r"^s(\d)", r"\1", v)
        return "Apple", _clean(f"apple watch {v}"), attrs
    m = re.search(r"\bgalaxy\s+watch\s*(\d)?\s*(classic|pro|ultra|active\s*\d?)?", t)
    if m:
        return "Samsung", _clean(f"galaxy watch {m.group(1) or ''} {m.group(2) or ''}"), attrs
    return None


_DETECTORS = {
    "phones": _phone_model,
    "tablets": _tablet_model,
    "laptops": _laptop_model,
    "gpu": _gpu_model,
    "consoles": _console_model,
    "audio": _audio_model,
    "watches": _watch_model,
}


# --------------------------------------------------------------------------- память и АКБ

def _gb(value: str, unit: str) -> int:
    return int(value) * (1024 if unit == "tb" else 1)


_VALID_RAM = {1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 24, 32, 64}


def _memory(t: str, model_numbers: frozenset[str] = frozenset()) -> tuple[int | None, int | None]:
    """(storage_gb, ram_gb) из нормализованного текста.

    model_numbers — числа из названия модели: в «iPhone 12/128» 12 — это модель, а не ОЗУ.
    """
    storage = ram = None
    m = re.search(r"(?<![\d.,])(\d{1,2})\s*(?:/|\+|\\)\s*(\d{1,4})\s*(gb|tb)?(?![\d])", t)
    if m:
        r, s = int(m.group(1)), _gb(m.group(2), m.group(3) or ("tb" if int(m.group(2)) <= 4 else "gb"))
        if s in _VALID_STORAGE and s > r:
            if m.group(1) in model_numbers or r not in _VALID_RAM:
                return s, None
            return s, r

    m = re.search(r"(?:оперативн\w*(?:\s+памят\w*)?|озу|\bram)\s*[:\-]?\s*(\d{1,2})\s*gb", t) or re.search(
        r"\b(\d{1,2})\s*gb\s*(?:оперативн\w*|озу|ram\b|ddr\d?|lpddr\d?)", t
    )
    ram_span = None
    if m:
        ram, ram_span = int(m.group(1)), m.span()

    m = re.search(
        r"(?:встроенн\w*(?:\s+памят\w*)?|накопител\w*|\bssd|\bnvme|\bпзу|\brom|объем\w*(?:\s+памят\w*)?)"
        r"\s*[:\-]?\s*(\d{1,4})\s*(gb|tb)?",
        t,
    ) or re.search(r"\b(\d{1,4})\s*(gb|tb)?\s*(?:ssd|nvme|встроенн\w*|пзу|rom\b)", t)
    if m:
        s = _gb(m.group(1), m.group(2) or "gb")
        if s >= 16:
            return s, ram

    values = []
    for mm in re.finditer(r"\b(\d{1,4})\s*(gb|tb)\b", t):
        if ram_span and ram_span[0] <= mm.start() < ram_span[1]:
            continue
        values.append(_gb(mm.group(1), mm.group(2)))
    storage_like = [v for v in values if v in _VALID_STORAGE and v >= 32]
    if storage_like:
        storage = max(storage_like)
        small = [v for v in values if v <= 24]
        if ram is None and small:
            ram = min(small)
    return storage, ram


def battery_health(t: str) -> int | None:
    m = re.search(r"износ\w*\s*(?:акб|аккумулятор\w*|батаре\w*)?\D{0,10}?(\d{1,2})\s*%", t)
    if m and int(m.group(1)) <= 50:
        return 100 - int(m.group(1))
    m = re.search(
        r"(?:акб|аккумулятор\w*|батаре\w*|емкост\w*|battery|здоровь\w*)\D{0,20}?(\d{2,3})\s*%", t
    ) or re.search(r"(\d{2,3})\s*%\s*(?:акб|аккумулятор\w*|батаре\w*|емкост\w*)", t)
    if m:
        v = int(m.group(1))
        return v if 50 <= v <= 100 else None
    return None


# --------------------------------------------------------------------------- точка входа

def extract_specs(title: str, description: str = "", params: dict[str, str] | None = None,
                  profile: str = "generic") -> Specs:
    params = params or {}
    params_text = normalize(" ; ".join(f"{k}: {v}" for k, v in params.items()))
    head = normalize(title)
    desc = normalize(description)
    head_params = f"{head} ; {params_text}" if params_text else head

    order = [profile] if profile in _DETECTORS else []
    order += [p for p in _DETECTORS if p not in order]
    brand = model = None
    attrs: dict[str, str] = {}
    used_profile = profile
    for p in order:
        found = _DETECTORS[p](head) or (_DETECTORS[p](params_text) if params_text else None)
        if found:
            brand, model, attrs = found
            if profile == "generic":
                used_profile = p
            break

    storage = ram = None
    if used_profile not in {"gpu", "audio", "watches"}:
        model_numbers = frozenset(re.findall(r"\d+", model or ""))
        storage, ram = _memory(head_params, model_numbers)
        if storage is None and used_profile in {"phones", "tablets"}:
            bare = [int(x) for x in re.findall(r"(?<![\d/.,])(64|128|256|512)(?![\d.,]|\s*(?:mm|mah|мач|hz|гц))", head)
                    if x not in model_numbers]
            storage = bare[0] if bare else None
        if storage is None and desc:
            storage, ram2 = _memory(desc, model_numbers)
            ram = ram or ram2
    if used_profile == "gpu":
        storage = ram = None

    return Specs(
        profile=used_profile,
        brand=brand,
        model=model or None,
        storage_gb=storage,
        ram_gb=ram if used_profile in {"phones", "tablets", "laptops"} else None,
        battery=battery_health(f"{head_params} ; {desc}"),
        attrs=attrs,
        tokens=tokens(title),
    )


def typical_battery(specs_list: list[Specs]) -> float | None:
    values = [s.battery for s in specs_list if s.battery]
    return median(values) if len(values) >= 2 else None
