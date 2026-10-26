"""Меню в терминале: всё выбирается цифрами, команды запоминать не нужно.

Открывается командой `python -m avito_flip` без аргументов или скриптами start.bat / start.sh.
Ответы превращаются в обычную команду (scan, analyze, watch, demo), её же меню и показывает —
чтобы потом можно было запускать без вопросов.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .config import Settings, enable_browser_mode, load_settings
from .fetcher import EXIT_BLOCKED, playwright_hint

REGIONS = [
    ("Москва", "moskva"),
    ("Санкт-Петербург", "sankt-peterburg"),
    ("Вся Россия", "all"),
    ("Екатеринбург", "ekaterinburg"),
    ("Новосибирск", "novosibirsk"),
    ("Казань", "kazan"),
    ("Нижний Новгород", "nizhniy_novgorod"),
    ("Краснодар", "krasnodar"),
]

# Команды, которые ходят на сайт: им при блокировке можно добавить --browser.
_ONLINE_COMMANDS = {"scan", "analyze", "watch"}

_YES = {"д", "да", "y", "yes", "1", "+"}
_NO = {"н", "нет", "n", "no", "0", "-"}


def parse_amount(text: str) -> int | None:
    """«40000», «40 000», «40к», «1.5к» -> рубли; пустая строка -> None."""
    t = text.strip().lower().replace(" ", "").replace(" ", "").replace(",", ".").rstrip("₽р")
    if not t:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(к|k|т|тыс)?", t)
    if not m:
        raise ValueError(text)
    value = float(m.group(1)) * (1000 if m.group(2) else 1)
    return int(round(value))


def command_line(argv: list[str]) -> str:
    # start.bat / start.sh сообщают, как их вызывать; иначе — запуск модулем.
    prefix = os.environ.get("AVITO_FLIP_CMD") or "python -m avito_flip"
    parts = [f'"{a}"' if (" " in a or not a) else a for a in argv]
    return prefix + " " + " ".join(parts)


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def system_browser_found() -> bool:
    """Есть ли Chrome или Edge, которые Playwright может запустить без скачивания своего Chromium."""
    if sys.platform == "win32":
        return True  # Microsoft Edge встроен в Windows 10/11
    if sys.platform == "darwin":
        return any(Path(p).exists() for p in ("/Applications/Google Chrome.app", "/Applications/Microsoft Edge.app"))
    return any(shutil.which(b) for b in ("google-chrome", "google-chrome-stable", "microsoft-edge-stable"))


def bundled_chromium_found() -> bool:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom:
        root = Path(custom)
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"
    return root.is_dir() and any(p.name.startswith("chromium") for p in root.iterdir())


def install_playwright(menu: Menu) -> bool:
    """Поставить Playwright (и при необходимости Chromium) в тот же Python, что запускает парсер."""
    if not playwright_installed():
        if not menu.yes("Для этого нужно один раз установить Playwright (~40 МБ). Установить?"):
            return False
        menu.say("Устанавливаю Playwright…")
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "playwright"]
        if subprocess.run(cmd).returncode != 0:
            menu.say("Не удалось установить Playwright.\n" + playwright_hint())
            return False
        importlib.invalidate_caches()
    if not system_browser_found() and not bundled_chromium_found():
        menu.say("На компьютере не найден Google Chrome или Microsoft Edge.")
        if not menu.yes("Скачать браузер Chromium для парсера (~150 МБ, один раз)?"):
            return False
        if subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode != 0:
            menu.say("Не удалось скачать Chromium. Установите Google Chrome и попробуйте снова.")
            return False
    return True


class Menu:
    def __init__(self, settings: Settings, run: Callable[[list[str]], int], ask: Callable[[str], str] = input,
                 say: Callable[[str], None] = print, installer: Callable[[Menu], bool] = install_playwright,
                 config_path: str | Path = "config.yaml"):
        self.s, self.run, self._ask, self.say = settings, run, ask, say
        self.install = installer
        self.config_path = config_path
        # После удачного поиска через браузер остальные действия сеанса тоже идут через него.
        self.browser = settings.network.browser

    # ------------------------------------------------------------ вопросы

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        return self._ask(f"{prompt}{suffix}: ").strip() or default

    def choose(self, title: str, options: list[str], default: int = 1) -> int:
        """Номер варианта, начиная с 1."""
        self.say(f"\n{title}")
        for i, opt in enumerate(options, 1):
            self.say(f"  {i}. {opt}")
        while True:
            answer = self.ask("Номер", str(default))
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer)
            self.say(f"Введите число от 1 до {len(options)}")

    def amount(self, prompt: str, default: int | None = None) -> int | None:
        while True:
            answer = self.ask(prompt, str(default) if default is not None else "")
            try:
                return parse_amount(answer)
            except ValueError:
                self.say("Введите сумму числом, например 40000 или 40к (Enter — пропустить)")

    def yes(self, prompt: str, default: bool = True) -> bool:
        while True:
            answer = self.ask(f"{prompt} (да/нет)", "да" if default else "нет").lower()
            if answer in _YES:
                return True
            if answer in _NO:
                return False
            self.say("Ответьте «да» или «нет»")

    # ------------------------------------------------------------ общие шаги

    def category(self) -> str:
        keys = list(self.s.categories)
        options = []
        for key in keys:
            c = self.s.categories[key]
            hint = f" — например: {', '.join(c.queries[:3])}" if c.queries else ""
            options.append(f"{c.title}{hint}")
        return keys[self.choose("Категория:", options) - 1]

    def queries(self, category_key: str) -> list[str]:
        c = self.s.categories[category_key]
        if c.queries:
            prompt = f"Что ищем? Несколько запросов — через запятую. Enter — {', '.join(c.queries)}"
        else:
            prompt = "Что ищем? (например: iphone 13; несколько — через запятую)"
        while True:
            answer = self.ask(prompt)
            found = [q.strip() for q in answer.split(",") if q.strip()]
            if found or c.queries:
                return found
            self.say("В этой категории нет запросов по умолчанию — введите, что искать")

    def region(self) -> str:
        options = [f"{name} ({slug})" for name, slug in REGIONS]
        slugs = [slug for _, slug in REGIONS]
        if self.s.region not in slugs:
            options.insert(0, f"Из настроек ({self.s.region})")
            slugs.insert(0, self.s.region)
        options.append("Другой город — ввести, как в адресе Авито (например rostov-na-donu)")
        n = self.choose("Где покупаем:", options, default=slugs.index(self.s.region) + 1)
        if n <= len(slugs):
            return slugs[n - 1]
        while True:
            slug = self.ask("Город латиницей, как в адресе avito.ru/<город>/...").lower()
            if re.fullmatch(r"[a-z0-9_-]+", slug):
                return slug
            self.say("Только латинские буквы, цифры, «-» и «_»")

    def _run(self, argv: list[str]) -> int:
        try:
            return self.run(argv)
        except KeyboardInterrupt:
            self.say("\nПрервано.")
            return 130
        except SystemExit as e:  # argparse при неверных аргументах
            return int(e.code or 0)

    def execute(self, argv: list[str]) -> int:
        online = argv[0] in _ONLINE_COMMANDS
        if online and self.browser and "--browser" not in argv:
            argv = [*argv, "--browser"]
        self.say(f"\nКоманда для повторного запуска без вопросов:\n  {command_line(argv)}\n")
        code = self._run(argv)
        if code != EXIT_BLOCKED or not online or "--browser" in argv:
            return code
        self.say(
            "\nАвито не пустил обычные запросы. Можно искать через настоящий браузер (Chrome или Edge): "
            "откроется его окно, страницы будут загружаться в нём. Если сайт попросит проверку "
            "«я не робот», пройдите её в этом окне — парсер подождёт и продолжит сам.\n"
            "Если на компьютере включён VPN, сначала лучше выключить его."
        )
        if not (self.yes("Попробовать через браузер?") and self.install(self)):
            return code
        argv = [*argv, "--browser"]
        self.say(f"\nКоманда для повторного запуска без вопросов:\n  {command_line(argv)}\n")
        code = self._run(argv)
        if code != EXIT_BLOCKED:
            self.browser = True
            if self.yes("Через браузер получилось. Всегда искать так (сохранить в config.yaml)?"):
                try:
                    saved = enable_browser_mode(self.config_path)
                    self.say(f"Сохранено в {saved}: дальше поиск сразу идёт через браузер.")
                except (OSError, ValueError) as e:  # ConfigError и ошибки YAML
                    self.say(f"Не удалось сохранить ({e}). Впишите в config.yaml вручную:\n"
                             "  network:\n    browser: true")
            else:
                self.say("Хорошо: до выхода из меню поиск идёт через браузер.")
        else:
            self.say(
                "\nАвито не пустил и настоящий браузер. Если проверку «я не робот» в окне пройти не удалось "
                "или там было «Доступ ограничен» — значит, Авито временно закрыл доступ вашему интернет-адресу "
                "(так бывает после частых запросов, а адреса VPN блокируются надолго). Что делать:\n"
                "  • откройте avito.ru в своём обычном браузере: если и там «Доступ ограничен», блок на адресе;\n"
                "  • подождите несколько часов или подключитесь через другую сеть — например, раздайте "
                "интернет с телефона;\n"
                "  • если в обычном браузере Авито открывается, запустите поиск ещё раз и пройдите проверку в окне."
            )
        return code

    # ------------------------------------------------------------ действия

    def scan(self) -> int:
        key = self.category()
        queries = self.queries(key)
        budget = self.amount("Бюджет: максимальная цена покупки, ₽ (Enter — без ограничения)")
        region = self.region()
        min_profit = self.amount("Минимальная прибыль с одной сделки, ₽", int(self.s.economics.min_profit))
        open_report = self.yes("Открыть отчёт в браузере после поиска?")
        argv = ["scan", "-c", key]
        for q in queries:
            argv += ["-q", q]
        if budget:
            argv += ["--max-price", str(budget)]
        if region != self.s.region:
            argv += ["--region", region]
        if min_profit is not None and min_profit != int(self.s.economics.min_profit):
            argv += ["--min-profit", str(min_profit)]
        if open_report:
            argv.append("--open")
        return self.execute(argv)

    def analyze(self) -> int:
        while True:
            url = self.ask("Ссылка на объявление Авито")
            if re.match(r"https?://(?:www\.|m\.)?avito\.ru/\S+", url):
                break
            self.say("Нужна ссылка вида https://www.avito.ru/moskva/telefony/..._1234567890")
        argv = ["analyze", url]
        if self.yes("Открыть отчёт в браузере?"):
            argv.append("--open")
        return self.execute(argv)

    def watch(self) -> int:
        key = self.category()
        queries = self.queries(key)
        budget = self.amount("Бюджет: максимальная цена покупки, ₽ (Enter — без ограничения)")
        region = self.region()
        every = self.amount("Как часто проверять, минут", 15) or 15
        argv = ["watch", "-c", key]
        for q in queries:
            argv += ["-q", q]
        if budget:
            argv += ["--max-price", str(budget)]
        if region != self.s.region:
            argv += ["--region", region]
        argv += ["--every", str(max(every, 5))]
        if self.s.telegram.get("token") and self.s.telegram.get("chat_id"):
            if self.yes("Присылать выгодные объявления в Telegram?"):
                argv.append("--telegram")
        else:
            self.say("Подсказка: чтобы получать уведомления в Telegram, впишите telegram.token и "
                     "telegram.chat_id в config.yaml.")
        self.say("Остановить наблюдение — Ctrl+C.")
        return self.execute(argv)

    def demo(self) -> int:
        argv = ["demo"]
        if self.yes("Открыть отчёт в браузере?"):
            argv.append("--open")
        return self.execute(argv)

    def loop(self) -> int:
        actions = [
            ("Найти выгодные объявления для перепродажи", self.scan),
            ("Оценить объявление по ссылке", self.analyze),
            ("Следить за новыми объявлениями (проверка каждые N минут)", self.watch),
            ("Демо на вымышленных данных (без интернета)", self.demo),
            ("Выход", None),
        ]
        self.say(f"\n=== Поиск товаров на Авито для перепродажи · версия {__version__} ===")
        self.say(f"Регион по умолчанию: {self.s.region}; минимальная прибыль: {int(self.s.economics.min_profit)} ₽"
                 + ("; поиск через браузер" if self.browser else "") + ". Поменять можно в config.yaml.")
        while True:
            try:
                n = self.choose("Что сделать?", [title for title, _ in actions])
                action = actions[n - 1][1]
                if action is None:
                    return 0
                action()
                self.ask("\nEnter — вернуться в меню")
            except (KeyboardInterrupt, EOFError):
                self.say("\nВыход.")
                return 0


def run_menu(run: Callable[[list[str]], int], config: str | None = None) -> int:
    try:
        settings = load_settings(config)
    except (OSError, ValueError) as e:
        print(f"Ошибка настроек: {e}")
        return 2
    return Menu(settings, run, config_path=config or "config.yaml").loop()
