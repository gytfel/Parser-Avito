"""Поиск дефектов, плюсов и рисков в тексте объявления.

Каждое правило — набор регулярных выражений. Совпадение не засчитывается, если
оно отрицается: «без сколов и царапин», «царапин нет», «экран не разбит».
Скидки (discount) — доля рыночной цены исправного товара, которую теряет
вещь с таким дефектом при продаже «как есть». repair_share — примерная стоимость
ремонта в долях той же цены (None — ремонт нецелесообразен или непредсказуем).
Цифры — эвристика, их стоит подстроить под свою категорию.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEVERITY_LEVEL = {"minor": 1, "major": 2, "critical": 3}
SEVERITY_LABEL = {"minor": "косметика", "major": "серьёзный", "critical": "критичный"}


@dataclass(frozen=True)
class Rule:
    code: str
    label: str
    severity: str
    discount: float
    patterns: tuple[str, ...]
    repair_share: float | None = None
    # «phrase» — без проверки отрицания (сама фраза уже содержит «не»).
    negatable: bool = True
    profiles: frozenset[str] | None = None
    # Совпадение игнорируется, если рядом встречается этот шаблон (защитное стекло и т.п.).
    unless: str | None = None
    flag: str | None = None  # parts | lock | fake
    scalable: bool = False  # «мелкие царапины» / «сильные царапины» меняют скидку


@dataclass
class Finding:
    code: str
    label: str
    severity: str
    discount: float
    repair_share: float | None
    evidence: str

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABEL[self.severity]


@dataclass
class Bonus:
    code: str
    label: str
    value: float
    evidence: str


@dataclass
class DefectReport:
    defects: list[Finding] = field(default_factory=list)
    bonuses: list[Bonus] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    is_fake: bool = False
    is_parts: bool = False
    is_locked: bool = False
    is_new: bool = False
    haggle: bool | None = None  # True — торг уместен, False — без торга

    @property
    def level(self) -> int:
        return max((SEVERITY_LEVEL[d.severity] for d in self.defects), default=0)

    @property
    def discount(self) -> float:
        keep = 1.0
        for d in self.defects:
            keep *= 1 - d.discount
        total = 1 - keep
        if self.is_parts:
            total = max(total, 0.65)
        return min(total, 0.85)

    @property
    def repairable(self) -> bool:
        """Все серьёзные дефекты можно устранить ремонтом с предсказуемой ценой."""
        serious = [d for d in self.defects if d.severity != "minor"]
        return bool(serious) and all(d.repair_share is not None for d in serious) and not (
            self.is_parts or self.is_locked or self.is_fake
        )

    @property
    def repair_share(self) -> float:
        return sum(d.repair_share or 0 for d in self.defects if d.severity != "minor")

    @property
    def cosmetic_discount(self) -> float:
        """Скидка за то, что останется после ремонта (царапины, потёртости)."""
        keep = 1.0
        for d in self.defects:
            if d.severity == "minor":
                keep *= 1 - d.discount
        return 1 - keep

    @property
    def bonus(self) -> float:
        return min(sum(b.value for b in self.bonuses), 0.06)


# Необязательное слово между частями фразы, но не отрицание: «экран сильно разбит», но не «экран не разбит».
_W = r"(?:(?!не\b|ни\b|без\b)\w+\s+)?"

_COMP = (
    r"face\s*id|фейс\s*(?:айди|ид)|touch\s*id|тач\s*(?:айди|ид)|камер\w*|вспышк\w*|динамик\w*|микрофон\w*"
    r"|кнопк\w*|клавиш\w*|wi-?fi|вай[\s-]?фай|bluetooth|блютуз\w*|nfc|модем\w*|gps|вибр\w*|датчик\w*"
    r"|сенсор\w*|тачскрин\w*|тачпад\w*|клавиатур\w*|разъем\w*|порт\w*|usb|hdmi|дисковод\w*|привод\w*"
    r"|геймпад\w*|джойстик\w*|стик\w*|кулер\w*|вентилятор\w*|звук\w*|подсветк\w*|петл\w*|шарнир\w*"
)
_BROKEN = (
    r"не\s+работа\w*|не\s+работ\w*|неисправ\w*|сломан\w*|отвал\w*|глючит|глючат|барахл\w*|плохо\s+работа\w*"
    r"|не\s+реагиру\w*|не\s+срабатыва\w*|не\s+ловит|не\s+видит"
)

# (метка, скидка, доля ремонта) по группам компонентов.
_COMPONENTS: list[tuple[str, str, float, float | None]] = [
    (r"face|фейс|touch|тач\s*(?:айди|ид)", "Face ID / Touch ID", 0.25, None),
    (r"камер|вспышк", "камера", 0.15, 0.10),
    (r"динамик|микрофон|звук", "звук / микрофон", 0.10, 0.06),
    (r"wi-?fi|вай|bluetooth|блютуз|nfc|модем|gps", "связь (Wi-Fi / BT / NFC / модем)", 0.20, 0.12),
    (r"сенсор|тачскрин", "сенсор", 0.30, 0.22),
    (r"тачпад|клавиатур|клавиш", "клавиатура / тачпад", 0.12, 0.08),
    (r"кнопк", "кнопки", 0.08, 0.05),
    (r"вибр|датчик", "вибро / датчики", 0.06, 0.04),
    (r"разъем|порт|usb|hdmi", "разъём / порт", 0.08, 0.05),
    (r"дисковод|привод", "дисковод", 0.15, 0.08),
    (r"геймпад|джойстик|стик", "геймпад / стики", 0.06, 0.04),
    (r"кулер|вентилятор", "охлаждение", 0.05, 0.03),
    (r"подсветк", "подсветка", 0.12, 0.08),
    (r"петл|шарнир", "петли", 0.08, 0.06),
]

_GPU_LIKE = frozenset({"gpu", "laptops", "consoles", "generic"})

RULES: list[Rule] = [
    # ---- критичные
    Rule("parts", "На запчасти / нерабочий", "critical", 0.70,
         (r"на\s+(?:запчаст\w*|з/ч|зч\b|детал\w*)", r"\bнерабоч\w*", r"\bне\s+рабоч\w*", r"\bдонор\w*"),
         negatable=False, flag="parts"),
    Rule("no_power", "Не включается / не загружается", "critical", 0.60,
         (r"не\s+(?:включа|запуска|загружа)\w*", r"\bкирпич\w*", r"бутлуп\w*|bootloop",
          r"циклическ\w*\s+перезагруз\w*", r"висит\s+на\s+(?:логотип|яблок|заставк)\w*"),
         negatable=False),
    Rule("water", "Был в воде / залит", "critical", 0.50,
         (r"\bутоп\w*", r"\bзалит\w*", r"после\s+(?:вод\w*|утоплени\w*|залити\w*)", r"попадал\w*\s+(?:в\s+)?вод\w*",
          r"\bокисл\w*", r"\bтонул\w*")),
    Rule("lock", "Заблокирован (iCloud / аккаунт)", "critical", 0.75,
         (r"(?:заблокирован\w*|залочен\w*|привязан\w*)\s+(?:к\s+|на\s+)?(?:icloud|айклауд\w*|аккаунт\w*|учетн\w*"
          r"|apple\s*id|google|гугл\w*|mi\s*аккаунт\w*)",
          r"(?:icloud|айклауд|apple\s*id)\s+(?:заблокирован\w*|залочен\w*|привязан\w*|не\s+отвязан\w*)",
          r"activation\s*lock", r"\bfrp\b", r"блокировк\w*\s+(?:активаци\w*|icloud|аккаунт\w*)"),
         flag="lock"),
    Rule("board", "Неисправна плата", "critical", 0.60,
         (r"(?:материнск\w*|системн\w*)\s+плат\w*\s+(?:\w+\s+){0,2}?(?:неисправн|сгорел|умерл|под\s+замен|не\s+работ)\w*",
          r"(?:неисправн\w*|сгоревш\w*|умерш\w*|мертв\w*)\s+(?:материнск\w*|системн\w*)\s+плат\w*",
          r"проблем\w*\s+(?:с|в)\s+(?:материнск\w*|плат\w*)", r"\bсгорел\w*")),
    # ---- серьёзные
    Rule("back_cover", "Повреждена задняя крышка / корпус", "major", 0.12,
         (r"(?:разбит\w*|треснут\w*|трещин\w*\s+на)\s+(?:задн\w*\s+(?:крышк|стекл|панел)\w*|крышк\w*|корпус\w*)",
          r"задн\w*\s+(?:крышк|стекл|панел)\w*\s+(?:разбит|треснут|треснул|в\s+трещин|с\s+трещин|поврежд)\w*"),
         repair_share=0.08),
    Rule("screen", "Разбит / треснут экран", "major", 0.35,
         (rf"(?:экран|дисплей|дисплэй|стекло|тачскрин|матриц\w*)\w*\s+{_W}(?:разбит|треснут|треснул|в\s+трещин"
          r"|с\s+трещин|поврежд|сломан)\w*",
          r"(?:разбит\w*|треснут\w*|треснувш\w*|поврежденн\w*)\s+(?:экран|дисплей|стекл|тачскрин|матриц)\w*",
          r"трещин\w*\s+(?:на\s+|по\s+)?(?:экран|диспле|стекл|матриц)\w*",
          r"(?:экран|диспле|дисплей|стекл|матриц)\w*\s+(?:есть\s+)?трещин\w*", r"\bпаутин\w*", r"\bразбит\w*",
          r"\bтреснут\w*", r"\bтреснул\w*"),
         repair_share=0.28, unless=r"защитн|пленк|бронестекл"),
    Rule("display", "Дефект изображения (полосы / пиксели / выгорание)", "major", 0.20,
         (r"бит\w*\s+пиксел\w*", r"полос\w*\s+(?:на\s+)?(?:экран|диспле|матриц)\w*",
          r"(?:экран|дисплей|матриц)\w*\s+(?:с\s+)?(?:полос|пятн)\w*", r"\bвыгор\w*",
          r"пятн\w*\s+на\s+(?:экран|диспле|матриц)\w*", r"(?:зелен|розов|желт)\w*\s+(?:полос|пятн|оттен)\w*"),
         repair_share=0.22),
    Rule("screen_replaced", "Менялся экран (возможно, не оригинал)", "minor", 0.10,
         (r"(?:замен\w*|менял\w*|поменян\w*|поменял\w*)\s+(?:экран|дисплей|модул|стекл|матриц)\w*",
          r"(?:экран|дисплей|модуль)\w*\s+(?:был\w*\s+)?(?:замен|менял|поменян)\w*",
          r"(?:неоригинальн\w*|не\s+оригинальн\w*|копи\w*|aaa|incell)\s+(?:экран|дисплей|модул)\w*",
          r"(?:экран|дисплей)\w*\s+(?:не\s+оригинал|неоригинал|копия|incell)\w*"),
         unless=r"защитн|пленк|бронестекл"),
    Rule("battery_bad", "Аккумулятор под замену", "major", 0.10,
         (rf"(?:акб|аккумулятор\w*|батаре\w*)\s+{_W}(?:под\s+замен\w*|вздут\w*|плохо\s+держ\w*|не\s+держ\w*"
          r"|быстро\s+сад\w*|садит\w*|изношен\w*|требует\s+(?:замен|сервис)\w*)",
          r"\bвздут\w*", r"быстро\s+(?:садит|разряжа)\w*",
          r"(?:требуется|нужна|необходима)\s+замен\w*\s+(?:акб|аккумулятор|батаре)\w*"),
         repair_share=0.10),
    Rule("battery_replaced", "Менялся аккумулятор", "minor", 0.03,
         (r"(?:акб|аккумулятор\w*|батаре\w*)\s+(?:был\w*\s+)?(?:замен[её]н\w*|менял\w*|поменян\w*)",
          r"(?:менял\w*|поменял\w*|замен[её]н\w*)\s+(?:акб|аккумулятор|батаре)\w*")),
    Rule("component", "Не работает: {}", "major", 0.15,
         (rf"(?:{_BROKEN})\s+{_W}(?:{_COMP})", rf"(?:{_COMP})\s+{_W}(?:{_BROKEN}|отсутству\w*|хрип\w*|шипит)",
          rf"(?:кроме|не\s+считая)\s+(?:{_COMP})"),
         repair_share=0.10, negatable=False),
    Rule("broken", "Неисправность (уточните у продавца)", "major", 0.25,
         (r"\bне\s+работа(?:ет|ют)\b", r"\bнеисправн\w*", r"\bсломан\w*", r"\bглючит\b", r"\bбарахлит\b",
          r"сам\w*\s+(?:выключа|перезагружа)\w*", r"перезагружа\w*\s+сам\w*")),
    Rule("carrier_lock", "Привязка к оператору / R-SIM", "major", 0.25,
         (r"\br-?sim\b", r"\bр-?сим\b", r"\bsim\s*lock\b", r"\bзалочен\w*",
          r"операторск\w*\s+(?:блок|верси|залоч)\w*", r"\bimei\s+(?:в\s+блок|заблок)\w*", r"серый\s+imei")),
    Rule("artifacts", "Артефакты изображения", "major", 0.45, (r"\bартефакт\w*",), profiles=_GPU_LIKE),
    Rule("reflow", "Ремонт чипа (прогрев / перепайка)", "major", 0.35,
         (r"\bперепа[яи]\w*", r"\bреболл\w*", r"\bпрогрев\w*", r"\bпрогрет\w*",
          r"отвал\w*\s+(?:чип|видеочип|гп|gpu)\w*"), profiles=_GPU_LIKE),
    Rule("mining", "После майнинга", "minor", 0.10,
         (r"(?:после|из[\s-]+под|с|из|в)\s+(?:майнинг\w*|ферм\w*)", r"\bмайнил\w*", r"\bмайнинг\w*"),
         profiles=frozenset({"gpu", "generic"})),
    # ---- косметика и мелочи
    Rule("overheat", "Греется / троттлинг", "minor", 0.07,
         (r"\bперегрев\w*", r"сильно\s+гре\w*", r"\bгреется\b", r"\bтрот+л\w*")),
    Rule("noise", "Шумит охлаждение", "minor", 0.04, (r"\b(?:шумит|гудит|трещит|свистит)\b", r"шумн\w*\s+(?:кулер|вентилятор)\w*")),
    Rule("drift", "Дрифт стиков", "minor", 0.05, (r"\bдрифт\w*", r"\bдрейф\w*", r"стик\w*\s+(?:уводит|плава|залипа)\w*")),
    Rule("backlash", "Люфт", "minor", 0.06, (r"\bлюфт\w*",)),
    Rule("repaired", "Был в ремонте / вскрывался", "minor", 0.04,
         (r"(?:был\w*\s+)?в\s+ремонт\w*", r"после\s+ремонт\w*", r"\bремонтировал\w*", r"\bвскрывал\w*",
          r"\bвскрыт\w*", r"\bразбирал\w*")),
    Rule("non_original", "Неоригинальные детали", "minor", 0.07,
         (r"\bнеоригинальн\w*", r"\bне\s+оригинальн\w*", r"\bнеродн\w*", r"\bне\s+родн\w*"),
         negatable=False, unless=r"зарядк|кабел|коробк|чех|блок|адаптер|ремеш|пленк|стекл"),
    Rule("refurbished", "Восстановленный (refurbished)", "minor", 0.06,
         (r"\bвосстановленн\w*", r"\brefurb\w*", r"\bреф\b", r"\bcpo\b")),
    Rule("crack", "Трещина", "major", 0.10, (r"\bтрещин\w*",), repair_share=0.06, unless=r"защитн|пленк|бронестекл"),
    Rule("dent", "Вмятины / погнут", "minor", 0.06, (r"\bвмятин\w*", r"\bпомят\w*", r"\bпогнут\w*", r"\bизогнут\w*"),
         scalable=True),
    Rule("chips", "Сколы", "minor", 0.04, (r"\bскол(?:ы|ов|а|ом|ами|ах|ик\w*)?\b", r"\bсколот\w*"), scalable=True),
    Rule("scratches", "Царапины", "minor", 0.04, (r"\bцарапин\w*", r"\bцарапк\w*", r"\bпоцарапан\w*", r"\bцарапы\b"),
         scalable=True),
    Rule("wear", "Потёртости", "minor", 0.03, (r"\bпотерт\w*", r"\bзатерт\w*", r"\bпотертост\w*"), scalable=True),
    Rule("bleed", "Засветы экрана", "minor", 0.05, (r"\bзасвет\w*",)),
    Rule("usage", "Следы использования", "minor", 0.02,
         (r"следы?\s+(?:использовани|эксплуатаци|носки)\w*", r"\bследов\s+использовани\w*")),
    Rule("defect", "Есть дефекты (уточните)", "minor", 0.05,
         (r"косметическ\w*\s+(?:дефект|недостат|изъян)\w*", r"\bдефект\w*", r"\bнедочет\w*", r"\bнюанс\w*")),
]

BONUS_RULES: list[tuple[str, str, float, tuple[str, ...]]] = [
    ("box", "Коробка / полный комплект", 0.02,
     (r"\bкоробк\w*", r"полн\w*\s+(?:комплект|комплектац)\w*", r"комплект\s+полный")),
    ("receipt", "Чек / документы", 0.015, (r"\bчек(?:и|ом|а|ов)?\b", r"\bдокумент\w*")),
    ("warranty", "Гарантия", 0.03, (r"\bгаранти\w*",)),
    ("ideal", "Отличное состояние (со слов продавца)", 0.02,
     (r"идеальн\w*\s+состояни\w*", r"\bв\s+идеал\w*", r"\bкак\s+нов\w*", r"отличн\w*\s+состояни\w*",
      rf"состояни\w*\s+{_W}(?:идеал|отличн)\w*")),
]

_FAKE = (
    r"\bреплик\w*", r"\bкопи[яию]\b(?!\s+(?:чек|документ|паспорт))", r"\bподделк\w*", r"\b(?:ааа|aaa)\b",
    r"\b1\s*[:к]\s*1\b", r"люкс\s*копи\w*", r"\bfake\b", r"\blux\b", r"(?:китайск\w*|китай)\s+(?:копи|верси|аналог|реплик)\w*",
    r"\bаналог\b", r"\bмуляж\w*", r"витринн\w*\s+(?:образец|муляж)\w*",
)
_PREPAY = (
    r"\bпредоплат\w*", r"отправлю\s+(?:только\s+)?после\s+оплат\w*", r"оплат\w*\s+(?:на|по)\s+(?:карт|номер|сбп)\w*",
    r"перевод\w*\s+на\s+карт\w*", r"\bпереведите\b",
)
_SEALED = (
    r"\bзапечатан\w*", r"(?:упаковк|коробк|пленк)\w*\s+не\s+вскрыт\w*", r"не\s+вскрыт\w*\s+(?:упаковк|коробк)\w*",
    r"\bне\s+распакован\w*", r"в\s+заводск\w*\s+пленк\w*",
)
_NO_HAGGLE = (r"без\s+торга", r"торг\s+не\s+уместен", r"торга\s+нет", r"цена\s+окончательн\w*", r"не\s+торгуюсь")
_HAGGLE = (r"\bторг\w*", r"\bуступлю\b", r"\bскидк\w*")

_NEG_BEFORE = {"без", "нет", "ни", "никаких", "никакой", "никаких-либо", "отсутствуют", "отсутствует", "ноль", "0"}
_NEG_AFTER = ("нет", "отсутству", "ноль", "истек", "закончил", "кончил")
_NEG_AFTER_NE = ("име", "обнаруж", "был", "замеч", "найд", "бер", "нуж", "прош", "треб", "возьм", "принима", "предусм")
_FILLER = (
    "и", "или", "а", "также", "видим", "заметн", "явн", "серьезн", "существен", "каки", "либо", "любы", "особ", "крупн",
    "мелк", "скол", "царап", "потерт", "трещин", "вмятин", "дефект", "след", "использован", "эксплуатац",
    "повреждени", "пятен", "бит", "пиксел", "сильн", "глубок", "на", "экран", "корпус", "диспле", "дисплей",
    "задн", "крышк", "одной", "одного", "един", "сзади", "спереди", "никак", "внешн", "косметич",
    "был", "ремонт", "вскрыт", "майнинг", "артефакт", "выгоран", "засвет", "люфт", "полос", "в", "по",
)
# Для плюсов («без коробки и документов») перечисление — только из комплектации.
_FILLER_BONUS = (
    "и", "или", "а", "также", "коробк", "чек", "документ", "зарядк", "кабел", "комплект", "аксессуар", "гаранти",
    "родн", "оригинальн", "заводск", "упаковк",
)
_SOFTER = ("мелк", "небольш", "незначит", "микро", "легк", "едва", "чуть", "пара", "немного", "мельчайш", "мин")
_STRONGER = ("сильн", "глубок", "много", "серьезн", "значительн", "крупн", "множеств", "заметн")


def _prep(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[\r\n]+", " . ", t)
    return re.sub(r"[ \t]+", " ", t)


def _words(segment: str) -> list[str]:
    """Слова и запятые: запятая важна, чтобы «без сколов, коробка» не отрицало коробку."""
    return re.findall(r"[a-zа-я0-9-]+|,", segment)


def _sentence_bounds(t: str, start: int, end: int) -> tuple[int, int]:
    left = max(t.rfind(c, 0, start) for c in ".!?;") + 1
    rights = [i for i in (t.find(c, end) for c in ".!?;") if i != -1]
    return left, min(rights) if rights else len(t)


def _is_filler(word: str, fillers: tuple[str, ...]) -> bool:
    return word == "," or any(word.startswith(f) for f in fillers)


def _genitive(word: str) -> bool:
    """«царапин», «сколов», «повреждений» — форма после «без», переносится через запятую."""
    return word.endswith(("ов", "ев", "ин", "ей", "ий", "ен", "ок"))


def _negated(t: str, start: int, end: int, fillers: tuple[str, ...] = None) -> bool:
    fillers = fillers or _FILLER
    left, right = _sentence_bounds(t, start, end)
    before = _words(t[left:start])[-8:]
    after = [w for w in _words(t[end:right])[:6] if w != ","]
    head = (_words(t[start:end]) or [""])[0]
    words_before = [w for w in before if w != ","]
    if words_before and words_before[-1] in {"не", "ни"}:
        return True
    if len(words_before) >= 2 and words_before[-2] in {"не", "ни"} and words_before[-1].startswith(("был", "разу")):
        return True
    # «без сколов, царапин и потёртостей»: между «без» и совпадением — только перечисление
    crossed_comma = False
    for i in range(len(before) - 1, -1, -1):
        w = before[i]
        if w == ",":
            crossed_comma = True
            continue
        if w in _NEG_BEFORE:
            return all(_is_filler(x, fillers) for x in before[i + 1:]) and (not crossed_comma or _genitive(head))
        if not _is_filler(w, fillers):
            break
    # «царапин нет», «сколов и трещин не имеет», «гарантия истекла»
    for i, w in enumerate(after):
        if w.startswith(_NEG_AFTER):
            return True
        if w == "не" and i + 1 < len(after) and after[i + 1].startswith(_NEG_AFTER_NE):
            return True
        if not _is_filler(w, fillers):
            break
    return False


def _evidence(t: str, start: int, end: int) -> str:
    left, right = _sentence_bounds(t, start, end)
    left = max(left, start - 60)
    right = min(right, end + 60)
    return re.sub(r"\s+", " ", t[left:right]).strip(" .,")


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in taken)


def _scale(t: str, start: int) -> float:
    before = _words(t[max(0, start - 30):start])[-2:]
    if any(w.startswith(_SOFTER) for w in before):
        return 0.5
    if any(w.startswith(_STRONGER) for w in before):
        return 1.6
    return 1.0


def _find(t: str, patterns: tuple[str, ...], negatable: bool = True,
          taken: list[tuple[int, int]] | None = None, unless: str | None = None,
          fillers: tuple[str, ...] | None = None):
    for p in patterns:
        for m in re.finditer(p, t):
            if taken is not None and _overlaps(m.span(), taken):
                continue
            if unless and re.search(unless, t[max(0, m.start() - 25):m.end() + 12]):
                continue
            if negatable and _negated(t, m.start(), m.end(), fillers):
                continue
            yield m


def _component_info(text: str) -> tuple[str, float, float | None]:
    for rx, label, discount, repair in _COMPONENTS:
        if re.search(rx, text):
            return label, discount, repair
    return "компонент", 0.15, 0.10


_CONDITION_PARTS = re.compile(r"требует\s+ремонт|на\s+запчаст|нерабоч|неисправ|сломан")
_CONDITION_FAIR = re.compile(r"удовлетворительн")
_CONDITION_NEW = re.compile(r"^(?:нов\w*|new)$")


def analyze_defects(text: str, profile: str = "generic", condition: str | None = None) -> DefectReport:
    """Разобрать текст объявления (заголовок + описание + параметры)."""
    t = _prep(text)
    report = DefectReport()
    taken: list[tuple[int, int]] = []
    seen_codes: set[str] = set()

    for rule in RULES:
        if rule.profiles and profile not in rule.profiles:
            continue
        for m in _find(t, rule.patterns, rule.negatable, taken, rule.unless):
            taken.append(m.span())
            if rule.code == "component":
                label, discount, repair = _component_info(m.group(0))
                code = f"component:{label}"
                if code in seen_codes:
                    continue
                finding = Finding(code, rule.label.format(label), rule.severity, discount, repair,
                                  _evidence(t, *m.span()))
            else:
                if rule.code in seen_codes:
                    continue
                discount = rule.discount * (_scale(t, m.start()) if rule.scalable else 1.0)
                code = rule.code
                finding = Finding(code, rule.label, rule.severity, round(discount, 3), rule.repair_share,
                                  _evidence(t, *m.span()))
            seen_codes.add(code)
            report.defects.append(finding)
            if rule.flag == "parts":
                report.is_parts = True
            elif rule.flag == "lock":
                report.is_locked = True

    for code, label, value, patterns in BONUS_RULES:
        m = next(_find(t, patterns, fillers=_FILLER_BONUS), None)
        if m:
            report.bonuses.append(Bonus(code, label, value, _evidence(t, *m.span())))

    if m := next(_find(t, _FAKE), None):
        report.is_fake = True
        report.risks.append(f"Похоже на копию / реплику / муляж: «{_evidence(t, *m.span())}»")
    if m := next(_find(t, _PREPAY), None):
        report.risks.append(f"Просит предоплату / перевод — частая схема мошенников: «{_evidence(t, *m.span())}»")
    if report.is_locked:
        report.risks.append("Устройство заблокировано — перепродать можно только на запчасти")
    if next(_find(t, _SEALED, negatable=False), None):
        report.is_new = True

    if next(_find(t, _NO_HAGGLE, negatable=False), None):
        report.haggle = False
    elif next(_find(t, _HAGGLE), None):
        report.haggle = True

    if re.search(r"\bобмен\w*", t) and not re.search(r"без\s+обмен|обмен\s+не", t):
        report.notes.append("Рассматривает обмен")

    if condition:
        c = condition.lower().replace("ё", "е").strip()
        if _CONDITION_PARTS.search(c) and not report.is_parts and report.level < 3:
            report.defects.append(Finding("condition_parts", f"Состояние «{condition}»", "critical", 0.60, None,
                                          f"состояние: {condition}"))
            report.is_parts = True
        elif _CONDITION_FAIR.search(c) and report.level < 2:
            report.defects.append(Finding("condition_fair", f"Состояние «{condition}»", "minor", 0.08, None,
                                          f"состояние: {condition}"))
        elif _CONDITION_NEW.match(c):
            report.is_new = True
    return report
