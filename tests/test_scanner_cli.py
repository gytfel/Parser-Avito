import time

import pytest

from avito_flip.cli import main
from avito_flip.config import ConfigError, Network, load_settings
from avito_flip.demo import DemoSource
from avito_flip.fetcher import BlockedError, DiskCache, Fetcher, FetchError, looks_blocked
from avito_flip.models import Listing
from avito_flip.scanner import Scanner


def listing(i, title, price, desc="Хорошее состояние", source="avito"):
    return Listing(source=source, id=str(i), title=title, price=price, description=desc, url=f"https://x/{i}")


def make_scanner(avito, youla=(), **overrides):
    s = load_settings()
    s.details_top = 0
    for k, v in overrides.items():
        setattr(s, k, v)
    sources = {"avito": DemoSource("avito", "Авито", list(avito)), "youla": DemoSource("youla", "Юла", list(youla))}
    return Scanner(s, sources=sources), s


def test_scan_filters_accessories_and_finds_deal():
    market = [listing(i, "iPhone 13, 128 ГБ", p) for i, p in enumerate([39000, 40000, 41000, 42000, 43000])]
    youla = [listing(50 + i, "Айфон 13 128гб", p, source="youla") for i, p in enumerate([40500, 41500])]
    extra = [
        listing(90, "Чехол для iPhone 13", 500),
        listing(91, "Куплю iPhone 13 дорого", 30000),
        listing(92, "iPhone 12, 128 ГБ", 20000),  # не по запросу
        listing(93, "iPhone 13, 128 ГБ", 30000, "Отличное состояние, коробка"),
    ]
    scanner, _ = make_scanner(market + extra, youla)
    r = scanner.scan(scanner.settings.category("phones"), "iphone 13")
    assert r.excluded == 2
    assert {it.listing.id for it in r.pool} == {str(i) for i in range(5)} | {"50", "51", "93"}
    best = r.deals[0]
    assert best.item.listing.id == "93" and best.verdict == "good"
    assert best.market.by_source == {"avito": 5, "youla": 2}


def test_scan_price_range_limits_candidates_not_market():
    market = [listing(i, "iPhone 13, 128 ГБ", p) for i, p in enumerate([39000, 40000, 41000, 42000, 43000])]
    scanner, _ = make_scanner(market + [listing(9, "iPhone 13, 128 ГБ", 30000)])
    r = scanner.scan(scanner.settings.category("phones"), "iphone 13", price_max=35000)
    assert [d.item.listing.id for d in r.deals] == ["9"]
    assert r.deals[0].market.n == 5


def test_details_are_fetched_for_top_candidates():
    market = [listing(i, "iPhone 13, 128 ГБ", p) for i, p in enumerate([39000, 40000, 41000, 42000, 43000])]
    scanner, _ = make_scanner(market + [listing(9, "iPhone 13, 128 ГБ", 30000)], details_top=2)
    r = scanner.scan(scanner.settings.category("phones"), "iphone 13")
    detailed = {d.item.listing.id for d in r.deals if d.item.listing.detailed}
    assert "9" in detailed and len(detailed) <= 2  # открываем только лучших, не всю выдачу


def test_looks_blocked():
    assert looks_blocked(403, "")
    assert looks_blocked(200, "<html><h2 class='firewall-title'>Доступ ограничен: проблема с IP</h2></html>")
    assert not looks_blocked(200, "<html>" + "x" * 50000 + "captcha</html>")


class FakeBackend:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def fetch(self, url, headers):
        self.calls += 1
        return self.responses.pop(0)

    def close(self):
        pass


def fast_net(tmp_path, **kw):
    return Network(delay_min=0, delay_max=0, retries=1, cache_dir=str(tmp_path), **kw)


def test_fetcher_caches_and_raises_on_block(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    f = Fetcher(fast_net(tmp_path))
    f._backend = FakeBackend([(200, "<html>ok</html>")])
    assert f.get("https://a") == "<html>ok</html>"
    assert f.get("https://a") == "<html>ok</html>"  # из кэша
    assert f._backend.calls == 1

    f._backend = FakeBackend([(429, ""), (403, "")])
    with pytest.raises(BlockedError):
        f.get("https://b")
    f._backend = FakeBackend([(404, "")])
    with pytest.raises(FetchError):
        f.get("https://c")


def test_disk_cache_ttl(tmp_path):
    c = DiskCache(tmp_path, ttl_minutes=0)
    c.set("u", "x")
    assert c.get("u") is None


def test_cli_demo_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["demo", "--html", "out/report.html"]) == 0
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "Кандидаты на покупку" in html and "iPhone 13" in html and "✅ Выгодно" in html
    assert "Смартфоны" in capsys.readouterr().out


def test_cli_categories(capsys):
    assert main(["categories"]) == 0
    assert "phones" in capsys.readouterr().out


def test_user_config_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("region: ekaterinburg\neconomics:\n  min_profit: 5000\n", encoding="utf-8")
    s = load_settings()
    assert s.region == "ekaterinburg" and s.economics.min_profit == 5000
    assert s.economics.sell_quantile == 0.4 and "phones" in s.categories
    (tmp_path / "config.yaml").write_text("economics:\n  min_proft: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()


def test_example_config_is_valid():
    from pathlib import Path

    s = load_settings(Path(__file__).parent.parent / "config.example.yaml")
    assert s.economics.min_profit == 3000 and s.network.delay_min == 5


def test_cli_reports_config_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "-c", "nope", "-q", "x"]) == 2
    assert "Нет категории" in capsys.readouterr().err
    assert main(["scan", "-c", "phones", "-q", "x", "--csv", "missing.csv"]) == 2
    assert "CSV-файл не найден" in capsys.readouterr().err


def test_console_escapes_listing_text():
    from rich.console import Console

    from avito_flip.report import print_result

    market = [listing(i, "iPhone 13, 128 ГБ", p) for i, p in enumerate([39000, 40000, 41000, 42000, 43000])]
    scanner, _ = make_scanner(market + [listing(9, "iPhone 13 [идеал] [/b] 128 ГБ", 30000, "без [торга]")])
    r = scanner.scan(scanner.settings.category("phones"), "iphone 13")
    console = Console(record=True, width=200)
    print_result(r, console, show_all=True)
    assert "[идеал] [/b]" in console.export_text()
