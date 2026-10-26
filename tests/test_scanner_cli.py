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
    return Network(**{"delay_min": 0, "delay_max": 0, "retries": 1, "cache_dir": str(tmp_path), **kw})


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


def test_block_reason_is_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    f = Fetcher(fast_net(tmp_path, retries=0))
    f._backend = FakeBackend([(403, "<html><title>Доступ ограничен: проблема с IP</title></html>")])
    with pytest.raises(BlockedError) as e:
        f.get("https://www.avito.ru/kazan/telefony?q=iphone")
    assert e.value.status == 403 and e.value.reason == "HTTP 403, «Доступ ограничен: проблема с IP»"
    assert "VPN" in str(e.value) and "--browser" in str(e.value)


def test_warm_up_opens_home_page_once(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    f = Fetcher(fast_net(tmp_path, retries=0))
    f._backend = FakeBackend([(200, "home"), (200, "<html>page</html>")])
    f.warm_up("https://www.avito.ru/")
    f.warm_up("https://www.avito.ru/")
    assert f._backend.calls == 1
    assert f.get("https://www.avito.ru/x") == "<html>page</html>"
    # главная с ошибкой сервера не мешает поиску, а блокировка — сразу видна
    g = Fetcher(fast_net(tmp_path, retries=0))
    g._backend = FakeBackend([(500, "")])
    g.warm_up("https://www.avito.ru/")
    h = Fetcher(fast_net(tmp_path, retries=0))
    h._backend = FakeBackend([(429, "")])
    with pytest.raises(BlockedError):
        h.warm_up("https://www.avito.ru/")


class BlockedSource(DemoSource):
    def search(self, *args, **kwargs):
        raise BlockedError("https://www.avito.ru/...\nОтвет сайта: HTTP 403", 403, "HTTP 403")


def test_scan_raises_blocked_when_nothing_loaded():
    s = load_settings()
    scanner = Scanner(s, sources={"avito": BlockedSource("avito", "Авито", []), "youla": DemoSource("youla", "Юла", [])})
    with pytest.raises(BlockedError) as e:
        scanner.scan(s.category("phones"), "iphone 13")
    assert e.value.status == 403
    assert "Авито: доступ ограничен" in str(e.value) and "Юла: объявлений не найдено" in str(e.value)


def test_cli_returns_exit_blocked(monkeypatch, tmp_path, capsys):
    import avito_flip.cli as cli
    from avito_flip.fetcher import EXIT_BLOCKED

    class FakeScanner:
        def __init__(self, *a, **kw):
            self.calls = 0

        def scan(self, *a, **kw):
            raise BlockedError("заблокировано", 429, "HTTP 429")

        def close(self):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "Scanner", FakeScanner)
    assert cli.main(["scan", "-c", "phones"]) == EXIT_BLOCKED
    # после блокировки остальные запросы категории не отправляются
    assert capsys.readouterr().err.count("заблокировано") == 1


def test_enable_browser_mode_keeps_user_settings(tmp_path):
    from avito_flip.config import enable_browser_mode

    new = tmp_path / "new.yaml"
    enable_browser_mode(new)
    assert load_settings(new).network.browser is True

    existing = tmp_path / "config.yaml"
    existing.write_text("region: kazan  # мой город\nnetwork:\n    delay_min: 6\n    browser: false  # пока нет\n"
                        "economics:\n  min_profit: 3000\n", encoding="utf-8")
    enable_browser_mode(existing)
    text = existing.read_text(encoding="utf-8")
    assert "# мой город" in text and "browser: true  # пока нет" in text
    s = load_settings(existing)
    assert (s.region, s.network.delay_min, s.network.browser, s.economics.min_profit) == ("kazan", 6, True, 3000)

    no_key = tmp_path / "no_key.yaml"
    no_key.write_text("network:\n  delay_min: 7\nregion: kazan\n", encoding="utf-8")
    enable_browser_mode(no_key)
    s = load_settings(no_key)
    assert (s.network.delay_min, s.network.browser, s.region) == (7, True, "kazan")

    no_network = tmp_path / "no_network.yaml"
    no_network.write_text("region: kazan\n", encoding="utf-8")
    enable_browser_mode(no_network)
    assert load_settings(no_network).network.browser is True


def test_enable_browser_mode_refuses_unusual_yaml(tmp_path):
    from avito_flip.config import enable_browser_mode

    flow = tmp_path / "config.yaml"
    original = "network: {delay_min: 6}\n"
    flow.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigError):
        enable_browser_mode(flow)
    assert flow.read_text(encoding="utf-8") == original  # файл возвращён как был
