"""Уведомления в Telegram для режима наблюдения."""

from __future__ import annotations

import html

from .pricing import Deal
from .scanner import source_title
from .text import fmt_rub


def deal_message(deal: Deal, query: str) -> str:
    x = deal.item.listing
    defects = ", ".join(f.label for f in deal.item.defects.defects) or "не найдены"
    lines = [
        f"<b>{html.escape(deal.verdict_label)}</b> · «{html.escape(query)}»",
        f"<a href=\"{html.escape(x.url)}\">{html.escape(x.title)}</a> ({source_title(x.source)})",
        f"Цена {fmt_rub(x.price)} → продать ≈ {fmt_rub(deal.sell_price)}",
        f"Прибыль ≈ {fmt_rub(deal.profit)} (ROI {deal.roi:.0%}), уверенность {deal.confidence_label}",
        f"Дефекты: {html.escape(defects)}",
    ]
    if deal.risks:
        lines.append("⚠ " + html.escape(deal.risks[0]))
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    import requests

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
        timeout=20,
    )
    r.raise_for_status()
