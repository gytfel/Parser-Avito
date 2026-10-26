"""Вывод результатов в терминал (rich, если установлен; иначе простой текст)."""

from __future__ import annotations

from ..pricing import Deal
from ..scanner import ScanResult, source_title
from ..text import fmt_rub

try:
    from rich import box
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table

    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

_VERDICT_STYLE = {"good": "bold green", "risky": "yellow", "maybe": "cyan", "bad": "red", "fake": "magenta",
                  "nodata": "dim"}


def make_console():
    return Console(highlight=False) if HAS_RICH else None


def _profit_text(deal: Deal) -> str:
    if deal.profit is None:
        return "—"
    sign = "+" if deal.profit > 0 else ""
    return sign + fmt_rub(deal.profit)


def short_risk(risk: str) -> str:
    """«Просит предоплату / перевод — частая схема…: «…»» -> «Просит предоплату / перевод»."""
    return risk.split(" — ")[0].split(": «")[0]


def _defects_short(deal: Deal) -> str:
    parts = [d.label for d in deal.item.defects.defects]
    parts += [f"⚠ {short_risk(r)}" for r in deal.risks]
    return ", ".join(parts) if parts else "не найдены"


def _sources_line(by_source: dict[str, int]) -> str:
    return ", ".join(f"{source_title(k)}: {v}" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]))


def _link(title: str, url: str) -> str:
    """Заголовок-ссылка; текст объявлений экранируется, чтобы «[идеал]» не ломал разметку rich."""
    if url.startswith("http") and "]" not in url:
        return f"[link={url}]{escape(title)}[/link]"
    return escape(title)


def pick_deals(result: ScanResult, show_all: bool) -> list[Deal]:
    if show_all:
        return result.deals
    good = result.good
    if good:
        return good
    return [d for d in result.deals if d.verdict == "maybe"]


def print_result(result: ScanResult, console=None, top: int = 20, show_all: bool = False, detail: int = 5) -> None:
    deals = pick_deals(result, show_all)[:top]
    if not HAS_RICH or console is None:
        _print_plain(result, deals)
        return

    console.rule(f"[bold]{escape(result.category.title)}[/] · «{escape(result.query)}»")
    console.print(
        f"Объявлений: {len(result.pool)} ({_sources_line(result.by_source)}); "
        f"кандидатов на покупку: {len(result.deals)}; отсеяно (аксессуары, «куплю»): {result.excluded}"
    )
    for w in result.warnings:
        console.print(f"[yellow]⚠ {escape(w)}[/]")

    if result.overview:
        t = Table(title="Рынок по моделям", box=box.SIMPLE_HEAD, title_justify="left")
        for col in ("Модель", "Объявл.", "Мин", "P25", "Медиана", "Макс", "Площадки"):
            t.add_column(col, justify="right" if col not in ("Модель", "Площадки") else "left")
        for row in result.overview[:10]:
            t.add_row(escape(row["name"]), str(row["n"]), fmt_rub(row["min"]), fmt_rub(row["p25"]),
                      fmt_rub(row["median"]), fmt_rub(row["max"]), _sources_line(row["sources"]))
        console.print(t)

    if not deals:
        console.print("[dim]Выгодных вариантов не найдено. Попробуйте --all, другой запрос, больше страниц "
                      "или снизьте economics.min_profit.[/]")
        return

    t = Table(title="Что можно купить на перепродажу", box=box.SIMPLE_HEAD, title_justify="left")
    t.add_column("#", justify="right")
    t.add_column("Вердикт", no_wrap=True)
    t.add_column("Товар", max_width=46)
    t.add_column("Цена", justify="right")
    t.add_column("Продать за", justify="right")
    t.add_column("Прибыль", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("Аналоги", justify="right")
    t.add_column("Увер.", justify="right")
    t.add_column("Дефекты / риски", max_width=34)
    for i, d in enumerate(deals, 1):
        x = d.item.listing
        t.add_row(
            str(i),
            f"[{_VERDICT_STYLE[d.verdict]}]{d.verdict_label}[/]",
            f"{_link(x.title, x.url)}\n[dim]{escape(d.item.specs.describe())} · {source_title(x.source)}[/]",
            fmt_rub(x.price),
            fmt_rub(d.sell_price),
            f"[{'green' if (d.profit or 0) > 0 else 'red'}]{_profit_text(d)}[/]",
            f"{d.roi:.0%}" if d.roi is not None else "—",
            str(d.market.n) if d.market else "0",
            f"{d.confidence:.0%}",
            escape(_defects_short(d)),
        )
    console.print(t)

    for i, d in enumerate(deals[:detail], 1):
        console.print(Panel(_detail_text(d), title=f"#{i} {escape(d.item.listing.title)}", title_align="left",
                            border_style=_VERDICT_STYLE[d.verdict].split()[-1]))


def _detail_text(d: Deal) -> str:
    x, m = d.item.listing, d.market
    lines = [f"[bold]{escape(d.item.specs.describe())}[/] · {source_title(x.source)} · {escape(x.location or '—')}"]
    if x.url:
        lines.append(_link(x.url, x.url))
    if m:
        lines.append(
            f"Рынок: {m.n} аналогов ({_sources_line(m.by_source)}), {fmt_rub(m.low)} – {fmt_rub(m.high)}, "
            f"медиана {fmt_rub(m.median)}; отброшено выбросов: {m.dropped}"
        )
        lines.append(
            f"Покупка ≈ {fmt_rub(d.buy_price)} → продажа ≈ {fmt_rub(d.sell_price)} "
            f"(исправный в таком состоянии ≈ {fmt_rub(d.clean_price)}); расходы {fmt_rub(d.costs)}"
            + (f", ремонт {fmt_rub(d.repair_cost)}" if d.repair_cost else "")
        )
        lines.append(f"Стратегия: {d.strategy}. Прибыль {_profit_text(d)}, уверенность {d.confidence_label}")
    for f in d.item.defects.defects:
        lines.append(f"[red]• {f.label}[/] ({f.severity_label}, −{f.discount:.0%}): «{escape(f.evidence)}»")
    if d.item.defects.bonuses:
        lines.append("[green]+ " + ", ".join(b.label for b in d.item.defects.bonuses) + "[/]")
    for n in d.notes:
        lines.append(f"[cyan]ℹ {escape(n)}[/]")
    for r in d.risks:
        lines.append(f"[yellow]⚠ {escape(r)}[/]")
    for n in d.item.defects.notes:
        lines.append(f"[dim]{escape(n)}[/]")
    if m:
        lines.append("[dim]Ближайшие аналоги:[/]")
        for c in m.comparables[:5]:
            cx = c.item.listing
            lines.append(f"  {fmt_rub(cx.price):>10}  {_link(cx.title, cx.url)} "
                         f"[dim]({source_title(cx.source)}, сходство {c.similarity:.0%})[/]")
    return "\n".join(lines)


def _print_plain(result: ScanResult, deals: list[Deal]) -> None:
    print(f"== {result.category.title} · «{result.query}» — объявлений: {len(result.pool)}")
    for w in result.warnings:
        print(f"! {w}")
    for i, d in enumerate(deals, 1):
        x = d.item.listing
        print(f"{i:>3}. {d.verdict_label:<22} {fmt_rub(x.price):>10} → {fmt_rub(d.sell_price):>10}  "
              f"прибыль {_profit_text(d):>10}  {x.title}  {x.url}")
