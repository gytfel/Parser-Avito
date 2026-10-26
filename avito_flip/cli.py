"""Командная строка: python -m avito_flip <команда> ...; без команды открывается меню."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings, SourceConfig, load_settings
from .fetcher import EXIT_BLOCKED, BlockedError, FetchError
from .report import make_console, print_result, save_csv, save_html, save_json
from .scanner import Scanner

try:
    from rich.markup import escape
except ImportError:  # pragma: no cover
    def escape(text: str) -> str:
        return text


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="путь к config.yaml (по умолчанию ./config.yaml, если есть)")
    p.add_argument("--region", help="регион покупки на Авито: moskva, sankt-peterburg, all ...")
    p.add_argument("--market-region", action="append", default=[], metavar="REGION",
                   help="доп. регион для оценки рынка/продажи (можно несколько раз)")
    p.add_argument("--pages", type=int, help="страниц выдачи Авито")
    p.add_argument("--min-profit", type=float, help="минимальная прибыль для «выгодно», ₽")
    p.add_argument("--details", type=int, help="сколько лучших объявлений открыть целиком")
    p.add_argument("--no-youla", action="store_true", help="не использовать Юлу")
    p.add_argument("--csv", action="append", default=[], metavar="FILE", help="CSV с ценами других площадок")
    p.add_argument("--browser", action="store_true", help="грузить страницы через Playwright (реже блокируют)")
    p.add_argument("--proxy", help="прокси http://user:pass@host:port")
    p.add_argument("--no-cache", action="store_true", help="не использовать кэш страниц")
    p.add_argument("--top", type=int, default=20, help="сколько вариантов показать")
    p.add_argument("--all", action="store_true", help="показать все варианты, а не только выгодные")
    p.add_argument("--html", help="куда сохранить HTML-отчёт (по умолчанию reports/...)")
    p.add_argument("--csv-out", help="сохранить результаты в CSV (для Excel)")
    p.add_argument("--json-out", help="сохранить результаты в JSON")
    p.add_argument("--open", action="store_true", help="открыть HTML-отчёт в браузере")
    p.add_argument("-v", "--verbose", action="store_true", help="показывать каждый запрос")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avito_flip",
        description="Поиск товаров на Авито для перепродажи: сравнение с рынком (Авито, Юла, свои CSV), "
                    "учёт дефектов и характеристик, оценка цены продажи и прибыли. "
                    "Без команды открывается меню.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("categories", help="список категорий и запросов по умолчанию")
    p.add_argument("--config")

    p = sub.add_parser("scan", help="найти выгодные объявления в категории")
    p.add_argument("-c", "--category", required=True, help="ключ категории (см. команду categories)")
    p.add_argument("-q", "--query", action="append", default=[],
                   help="поисковый запрос (можно несколько раз); без него — запросы категории из настроек")
    p.add_argument("--min-price", type=int, help="минимальная цена покупки")
    p.add_argument("--max-price", type=int, help="максимальная цена покупки (бюджет)")
    _common(p)

    p = sub.add_parser("analyze", help="оценить конкретное объявление по ссылке")
    p.add_argument("url")
    p.add_argument("-c", "--category", help="категория (по умолчанию определяется автоматически)")
    p.add_argument("-q", "--query", help="запрос для поиска аналогов (по умолчанию — модель из объявления)")
    _common(p)

    p = sub.add_parser("watch", help="следить за новыми объявлениями и сообщать о выгодных")
    p.add_argument("-c", "--category", required=True)
    p.add_argument("-q", "--query", action="append", default=[])
    p.add_argument("--min-price", type=int)
    p.add_argument("--max-price", type=int)
    p.add_argument("--every", type=float, default=15, help="интервал проверки, минут (по умолчанию 15)")
    p.add_argument("--telegram", action="store_true", help="слать уведомления в Telegram (токен в config.yaml)")
    _common(p)

    p = sub.add_parser("demo", help="показать работу на вымышленных данных (без интернета)")
    p.add_argument("--html", help="куда сохранить HTML-отчёт")
    p.add_argument("--all", action="store_true")
    p.add_argument("--open", action="store_true")
    p.add_argument("--config")
    return parser


def _apply_overrides(s: Settings, a: argparse.Namespace) -> Settings:
    if getattr(a, "region", None):
        s.region = a.region
    if getattr(a, "market_region", None):
        s.market_regions = [*s.market_regions, *a.market_region]
    if getattr(a, "pages", None):
        s.pages = a.pages
    if getattr(a, "min_profit", None) is not None:
        s.economics.min_profit = a.min_profit
    if getattr(a, "details", None) is not None:
        s.details_top = a.details
    if getattr(a, "no_youla", False):
        s.sources["youla"] = SourceConfig(enabled=False)
    if getattr(a, "csv", None):
        cfg = s.sources.setdefault("csv", SourceConfig())
        cfg.enabled, cfg.files = True, [*cfg.files, *a.csv]
    if getattr(a, "browser", False):
        s.network.browser = True
    if getattr(a, "proxy", None):
        s.network.proxy = a.proxy
    return s


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "_", text.lower()).strip("_")[:40] or "report"


def _save_reports(results, a, console, default_name: str) -> None:
    s_dir = Path(getattr(a, "_reports_dir", "reports"))
    html_path = Path(a.html) if a.html else s_dir / f"{default_name}_{datetime.now():%Y%m%d_%H%M}.html"
    save_html(results, html_path, show_all=getattr(a, "all", False))
    if console:
        console.print(f"\n[bold]HTML-отчёт:[/] {escape(str(html_path.resolve()))}")
    else:
        print(f"HTML: {html_path.resolve()}")
    if getattr(a, "csv_out", None):
        save_csv(results, a.csv_out)
        print(f"CSV: {Path(a.csv_out).resolve()}")
    if getattr(a, "json_out", None):
        save_json(results, a.json_out)
        print(f"JSON: {Path(a.json_out).resolve()}")
    if getattr(a, "open", False):
        webbrowser.open(html_path.resolve().as_uri())


# Сообщения, которые видны и без -v: о них пользователь должен знать (например, пройти проверку в браузере).
_ALWAYS_SHOWN = ("Открываю", "Слишком", "Ошибка", "Сайт просит", "Проверка пройдена", "Авито: на странице",
                 "Юла: на странице")


def _logger(console, verbose: bool):
    def log(msg: str) -> None:
        if not verbose and not msg.startswith(_ALWAYS_SHOWN):
            return
        if console:
            style = "yellow" if msg.startswith("Сайт просит") else "dim"
            console.print(f"[{style}]{escape(msg)}[/]")
        else:
            print(msg, file=sys.stderr)
    return log


def cmd_categories(a, s: Settings, console) -> int:
    for key, c in s.categories.items():
        queries = ", ".join(c.queries) or "—"
        print(f"{key:<10} {c.title:<22} профиль: {c.profile:<9} запросы: {queries}")
    return 0


def cmd_scan(a, s: Settings, console) -> int:
    category = s.category(a.category)
    queries = a.query or category.queries
    if not queries:
        print("Укажите запрос: -q \"iphone 13\"", file=sys.stderr)
        return 2
    scanner = Scanner(s, log=_logger(console, a.verbose), use_cache=not a.no_cache)
    results = []
    blocked = False
    try:
        for q in queries:
            if console:
                console.print(f"[bold]Ищу «{escape(q)}»[/] в «{escape(category.title)}» ({escape(s.region)})…")
            try:
                result = scanner.scan(category, q, price_min=a.min_price, price_max=a.max_price)
            except BlockedError as e:
                # Остальные запросы тоже упрутся в блокировку — не нагружаем сайт зря.
                print(f"Ошибка: {e}", file=sys.stderr)
                blocked = True
                break
            except FetchError as e:
                print(f"Ошибка: {e}", file=sys.stderr)
                continue
            results.append(result)
            print_result(result, console, top=a.top, show_all=a.all)
    finally:
        scanner.close()
    if not results:
        return EXIT_BLOCKED if blocked else 1
    a._reports_dir = s.reports_dir
    _save_reports(results, a, console, f"{category.key}_{_slug(queries[0])}")
    return 0


def cmd_analyze(a, s: Settings, console) -> int:
    category = s.category(a.category) if a.category else None
    scanner = Scanner(s, log=_logger(console, a.verbose), use_cache=not a.no_cache)
    try:
        deal, result = scanner.analyze_url(a.url, category, a.query, details=a.details or 0)
    except FetchError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return EXIT_BLOCKED if isinstance(e, BlockedError) else 1
    finally:
        scanner.close()
    result.deals = [deal]
    print_result(result, console, top=1, show_all=True, detail=1)
    a._reports_dir = s.reports_dir
    _save_reports([result], a, console, f"analyze_{deal.item.listing.id}")
    return 0


def cmd_watch(a, s: Settings, console) -> int:
    category = s.category(a.category)
    queries = a.query or category.queries
    token, chat = s.telegram.get("token"), s.telegram.get("chat_id")
    if a.telegram and not (token and chat):
        print("Для --telegram заполните telegram.token и telegram.chat_id в config.yaml", file=sys.stderr)
        return 2
    seen_path = Path(s.network.cache_dir) / f"seen_{category.key}.json"
    try:
        seen = set(json.loads(seen_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    # Кэш короче интервала, иначе новые объявления не появятся.
    s.network.cache_ttl_minutes = min(s.network.cache_ttl_minutes, max(a.every - 1, 0))
    scanner = Scanner(s, log=_logger(console, a.verbose), use_cache=not a.no_cache)
    from .notify import deal_message, send_telegram

    print(f"Наблюдение: {category.title}, запросы: {', '.join(queries)}; каждые {a.every:g} мин. Ctrl+C — выход.")
    try:
        while True:
            for q in queries:
                try:
                    result = scanner.scan(category, q, price_min=a.min_price, price_max=a.max_price)
                except FetchError as e:
                    print(f"[{datetime.now():%H:%M}] {q}: {e}", file=sys.stderr)
                    continue
                fresh = [d for d in result.good if d.item.listing.uid not in seen]
                for d in fresh:
                    seen.add(d.item.listing.uid)
                    x = d.item.listing
                    print(f"[{datetime.now():%H:%M}] {d.verdict_label} {x.title} — {x.price} ₽, "
                          f"прибыль ≈ {round(d.profit or 0)} ₽ {x.url}")
                    if a.telegram:
                        try:
                            send_telegram(token, chat, deal_message(d, q))
                        except Exception as e:
                            print(f"Telegram: {e}", file=sys.stderr)
                if not fresh:
                    print(f"[{datetime.now():%H:%M}] {q}: новых выгодных нет ({len(result.deals)} кандидатов)")
            seen_path.parent.mkdir(parents=True, exist_ok=True)
            seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")
            time.sleep(a.every * 60 * random.uniform(0.9, 1.1))
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        scanner.close()
    return 0


def cmd_demo(a, s: Settings, console) -> int:
    from .demo import demo_sources

    s.details_top = 3
    scanner = Scanner(s, sources=demo_sources())
    result = scanner.scan(s.category("phones"), "iphone 13")
    print_result(result, console, top=25, show_all=a.all)
    a._reports_dir = s.reports_dir
    a.csv_out = a.json_out = None
    _save_reports([result], a, console, "demo_iphone_13")
    return 0


COMMANDS = {"categories": cmd_categories, "scan": cmd_scan, "analyze": cmd_analyze, "watch": cmd_watch,
            "demo": cmd_demo}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from .interactive import run_menu

        return run_menu(run=main)
    a = build_parser().parse_args(argv)
    try:
        s = _apply_overrides(load_settings(a.config), a)
    except (OSError, ValueError) as e:  # ConfigError и ошибки YAML
        print(f"Ошибка настроек: {e}", file=sys.stderr)
        return 2
    console = make_console()
    try:
        return COMMANDS[a.command](a, s, console)
    except ConfigError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
