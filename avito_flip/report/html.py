"""HTML-отчёт: открывается в браузере, таблицу можно сортировать, детали раскрываются."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment

from ..scanner import ScanResult, source_title
from ..text import fmt_rub
from .console import pick_deals, short_risk

_TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Перепродажа: {{ title }}</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#6b7280; --line:#e5e7eb; --green:#15803d;
        --red:#b91c1c; --yellow:#a16207; --cyan:#0e7490; --accent:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --card:#171a21; --text:#e6e8ee; --muted:#9aa3b2; --line:#2a2f3a; --green:#4ade80;
          --red:#f87171; --yellow:#facc15; --cyan:#67e8f9; --accent:#60a5fa; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:1280px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; } h3 { font-size:15px; margin:20px 0 8px; }
.muted { color:var(--muted); } .warn { color:var(--yellow); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:4px 0; overflow-x:auto; }
table { width:100%; border-collapse:collapse; }
th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
th { font-weight:600; color:var(--muted); cursor:pointer; white-space:nowrap; user-select:none; }
th.num, td.num { text-align:right; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
.good { color:var(--green); font-weight:600; } .risky { color:var(--yellow); font-weight:600; }
.maybe { color:var(--cyan); } .bad { color:var(--red); } .fake { color:var(--red); font-weight:600; } .nodata { color:var(--muted); }
.pos { color:var(--green); font-weight:600; } .neg { color:var(--red); }
.spec { color:var(--muted); font-size:12px; }
.card table { min-width:760px; }
.nowrap { white-space:nowrap; } td.title { min-width:220px; }
.more-btn { display:block; margin-top:2px; padding:0; border:0; background:none; color:var(--accent); cursor:pointer; font:inherit; }
tr.more > td { background:var(--bg); }
.grid { display:grid; grid-template-columns:minmax(0, 3fr) minmax(0, 2fr); gap:8px 32px; padding:4px 2px 8px; }
@media (max-width: 800px) { .grid { grid-template-columns:minmax(0, 1fr); } }
.grid h4 { margin:10px 0 4px; font-size:13px; } .grid ul { margin:4px 0; padding-left:18px; }
.comps .num { display:inline-block; min-width:76px; }
tbody.deal tr.main td { border-bottom:1px solid var(--line); } tbody.deal:last-child tr.main td { border-bottom:none; }
.chip { display:inline-block; padding:1px 8px; border-radius:999px; border:1px solid var(--line); margin:0 4px 4px 0; font-size:12px; }
.chip.d { border-color:var(--red); color:var(--red); } .chip.b { border-color:var(--green); color:var(--green); }
.chip.r { border-color:var(--yellow); color:var(--yellow); }
.toolbar { margin:8px 0; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
</style>
</head>
<body>
<main>
<h1>Поиск товаров на перепродажу</h1>
<div class="muted">Сформировано {{ now }}. Цены рынка — по активным объявлениям; реальные сделки обычно на 5–10% ниже.</div>
{% for r in results %}
<h2>{{ r.category.title }} · «{{ r.query }}»</h2>
<div class="muted">Объявлений: {{ r.pool|length }} ({{ sources_line(r.by_source) }}); кандидатов: {{ r.deals|length }}; отсеяно аксессуаров и «куплю»: {{ r.excluded }}</div>
{% for w in r.warnings %}<div class="warn">⚠ {{ w }}</div>{% endfor %}

{% if r.overview %}
<h3>Рынок по моделям</h3>
<div class="card"><table class="sortable">
<thead><tr><th>Модель</th><th class="num">Объявлений</th><th class="num">Мин</th><th class="num">P25</th><th class="num">Медиана</th><th class="num">Макс</th><th>Площадки</th></tr></thead>
<tbody>
{% for row in r.overview[:15] %}
<tr><td>{{ row.name }}</td><td class="num">{{ row.n }}</td><td class="num" data-v="{{ row.min }}">{{ rub(row.min) }}</td>
<td class="num" data-v="{{ row.p25 }}">{{ rub(row.p25) }}</td><td class="num" data-v="{{ row.median }}">{{ rub(row.median) }}</td>
<td class="num" data-v="{{ row.max }}">{{ rub(row.max) }}</td><td>{{ sources_line(row.sources) }}</td></tr>
{% endfor %}
</tbody></table></div>
{% endif %}

<h3>Кандидаты на покупку</h3>
{% set deals = pick(r) %}
{% if not deals %}<div class="muted">Выгодных вариантов не найдено.</div>{% else %}
<div class="card"><table class="sortable grouped">
<thead><tr><th class="num">#</th><th>Вердикт</th><th>Товар</th><th class="num">Цена</th><th class="num">Продать за</th>
<th class="num">Прибыль</th><th class="num">ROI</th><th class="num">Аналоги</th><th class="num">Уверенность</th><th>Дефекты / плюсы</th></tr></thead>
{% for d in deals %}{% set x = d.item.listing %}
<tbody class="deal">
<tr class="main">
<td class="num" data-v="{{ loop.index }}">{{ loop.index }}</td>
<td class="{{ d.verdict }} nowrap" data-v="{{ d.score if d.score > -1e18 else -1e18 }}">{{ d.verdict_label }}</td>
<td class="title">{% if x.url.startswith('http') %}<a href="{{ x.url }}" target="_blank" rel="noopener">{{ x.title }}</a>{% else %}{{ x.title }}{% endif %}
<div class="spec">{{ d.item.specs.describe() }} · {{ src(x.source) }}{% if x.location %} · {{ x.location }}{% endif %}</div></td>
<td class="num" data-v="{{ x.price }}">{{ rub(x.price) }}</td>
<td class="num" data-v="{{ d.sell_price or 0 }}">{{ rub(d.sell_price) }}</td>
<td class="num {{ 'pos' if (d.profit or 0) > 0 else 'neg' }}" data-v="{{ d.profit or -1e12 }}">{{ profit(d) }}</td>
<td class="num" data-v="{{ d.roi or -99 }}">{{ '%.0f%%'|format(d.roi * 100) if d.roi is not none else '—' }}</td>
<td class="num" data-v="{{ d.market.n if d.market else 0 }}">{{ d.market.n if d.market else 0 }}</td>
<td class="num" data-v="{{ d.confidence }}">{{ '%.0f%%'|format(d.confidence * 100) }}</td>
<td>
{% for f in d.item.defects.defects %}<span class="chip d">{{ f.label }}</span>{% endfor %}
{% for rk in d.risks %}<span class="chip r">⚠ {{ short_risk(rk) }}</span>{% endfor %}
{% for b in d.item.defects.bonuses %}<span class="chip b">{{ b.label }}</span>{% endfor %}
<button class="more-btn" type="button">подробнее</button>
</td>
</tr>
<tr class="more" hidden><td colspan="10"><div class="grid">
<div>
{% if d.market %}{% set m = d.market %}
<h4>Расчёт</h4>
<div>Рынок: {{ m.n }} аналогов ({{ sources_line(m.by_source) }}), {{ rub(m.low) }} – {{ rub(m.high) }}, медиана {{ rub(m.median) }}; выбросов отброшено: {{ m.dropped }}</div>
<div>Покупка ≈ {{ rub(d.buy_price) }} → продажа ≈ {{ rub(d.sell_price) }} (исправный в таком состоянии ≈ {{ rub(d.clean_price) }}); расходы {{ rub(d.costs) }}{% if d.repair_cost %}, ремонт {{ rub(d.repair_cost) }}{% endif %}</div>
<div>Стратегия: <b>{{ d.strategy }}</b>; уверенность {{ d.confidence_label }}</div>
{% endif %}
{% if d.item.defects.defects %}<h4>Дефекты</h4><ul>
{% for f in d.item.defects.defects %}<li><span class="bad">{{ f.label }}</span> ({{ f.severity_label }}, −{{ '%.0f'|format(f.discount * 100) }}%): <span class="muted">«{{ f.evidence }}»</span></li>{% endfor %}
</ul>{% endif %}
{% if d.notes or d.item.defects.notes %}<h4>Поправки к цене</h4><ul>
{% for n in d.notes %}<li>{{ n }}</li>{% endfor %}
{% for n in d.item.defects.notes %}<li class="muted">{{ n }}</li>{% endfor %}
</ul>{% endif %}
{% if d.risks %}<h4>Риски</h4><ul>{% for rk in d.risks %}<li class="risky">⚠ {{ rk }}</li>{% endfor %}</ul>{% endif %}
</div>
<div>
{% if d.market %}<h4>Аналоги</h4><ul class="comps">
{% for c in d.market.comparables[:10] %}{% set cx = c.item.listing %}
<li><span class="num">{{ rub(cx.price) }}</span> {% if cx.url.startswith('http') %}<a href="{{ cx.url }}" target="_blank" rel="noopener">{{ cx.title }}</a>{% else %}{{ cx.title }}{% endif %}
<span class="muted">· {{ src(cx.source) }} · сходство {{ '%.0f'|format(c.similarity * 100) }}%</span></li>
{% endfor %}</ul>{% endif %}
</div>
</div></td></tr>
</tbody>
{% endfor %}
</table></div>
{% endif %}
{% endfor %}
<p class="muted">Оценка эвристическая: дефекты ищутся по тексту объявления, фото не анализируются. Перед покупкой проверяйте товар вживую,
IMEI/серийный номер и отвязку от аккаунтов.</p>
</main>
<script>
function sortValue(cell) {
  return cell.dataset.v !== undefined ? parseFloat(cell.dataset.v) : cell.textContent.trim().toLowerCase();
}
document.querySelectorAll('table.sortable').forEach(function (table) {
  var grouped = table.classList.contains('grouped');
  table.querySelectorAll('thead th').forEach(function (th, idx) {
    var asc = false;
    th.addEventListener('click', function () {
      asc = !asc;
      // В таблице кандидатов каждая сделка — свой <tbody> (строка + скрытые детали), сортируем их целиком.
      var units = grouped ? Array.prototype.slice.call(table.tBodies)
                          : Array.prototype.slice.call(table.tBodies[0].rows);
      units.sort(function (a, b) {
        var ra = grouped ? a.rows[0] : a, rb = grouped ? b.rows[0] : b;
        var x = sortValue(ra.cells[idx]), y = sortValue(rb.cells[idx]);
        if (x < y) return asc ? -1 : 1;
        if (x > y) return asc ? 1 : -1;
        return 0;
      });
      var parent = grouped ? table : table.tBodies[0];
      units.forEach(function (u) { parent.appendChild(u); });
    });
  });
});
document.querySelectorAll('.more-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var row = btn.closest('tbody').querySelector('tr.more');
    row.hidden = !row.hidden;
    btn.textContent = row.hidden ? 'подробнее' : 'скрыть';
  });
});
</script>
</body>
</html>
"""


def _sources_line(by_source: dict[str, int]) -> str:
    return ", ".join(f"{source_title(k)}: {v}" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]))


def _profit(d) -> str:
    if d.profit is None:
        return "—"
    return ("+" if d.profit > 0 else "") + fmt_rub(d.profit)


def render_html(results: list[ScanResult], show_all: bool = False) -> str:
    env = Environment(autoescape=True)
    tpl = env.from_string(_TEMPLATE)
    title = ", ".join(f"{r.category.title} «{r.query}»" for r in results) or "нет данных"
    return tpl.render(
        results=results,
        title=title,
        now=datetime.now().strftime("%d.%m.%Y %H:%M"),
        rub=fmt_rub,
        src=source_title,
        sources_line=_sources_line,
        profit=_profit,
        short_risk=short_risk,
        pick=lambda r: pick_deals(r, show_all),
    )


def save_html(results: list[ScanResult], path: str | Path, show_all: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(results, show_all), encoding="utf-8")
    return path
