import pytest

from avito_flip.config import load_settings
from avito_flip.interactive import Menu, command_line, parse_amount


def run_menu(answers, monkeypatch=None, **settings_overrides):
    s = load_settings()
    for k, v in settings_overrides.items():
        setattr(s, k, v)
    calls, said = [], []
    feed = iter(answers)

    def ask(prompt):
        said.append(prompt)
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    menu = Menu(s, run=lambda argv: calls.append(argv) or 0, ask=ask, say=said.append)
    assert menu.loop() == 0
    return calls, said


@pytest.mark.parametrize("text, value", [("40000", 40000), ("40 000", 40000), ("40к", 40000), ("1.5k", 1500),
                                         ("35 тыс", 35000), ("25000₽", 25000), ("", None)])
def test_parse_amount(text, value):
    assert parse_amount(text) == value


def test_parse_amount_rejects_text():
    with pytest.raises(ValueError):
        parse_amount("много")


def test_scan_flow_builds_command():
    # действие 1, категория 1 (смартфоны), два запроса, бюджет, регион 2 (СПб), прибыль 3000, без браузера, меню, выход
    calls, _ = run_menu(["1", "1", "iphone 13, iphone 14", "40к", "2", "3000", "нет", "", "5"])
    assert calls == [["scan", "-c", "phones", "-q", "iphone 13", "-q", "iphone 14", "--max-price", "40000",
                      "--region", "sankt-peterburg", "--min-profit", "3000"]]


def test_scan_flow_defaults():
    # Enter везде: категория 1, запросы категории, без бюджета, регион из настроек, прибыль по умолчанию, открыть отчёт
    calls, _ = run_menu(["", "", "", "", "", "", "", "", "5"])
    assert calls == [["scan", "-c", "phones", "--open"]]


def test_invalid_answers_are_asked_again():
    calls, said = run_menu(["1", "99", "4", "", "много", "", "", "", "нет", "", "5"])
    assert calls == [["scan", "-c", "gpu"]]
    assert any("от 1 до" in s for s in said) and any("числом" in s for s in said)


def test_custom_region_and_category_without_default_queries():
    keys = list(load_settings().categories)
    any_n = str(keys.index("any") + 1)
    other = "9"  # после 8 городов — «Другой город»
    calls, said = run_menu(["1", any_n, "", "велосипед", "", other, "Ростов!", "rostov-na-donu", "", "нет", "", "5"])
    assert calls == [["scan", "-c", "any", "-q", "велосипед", "--region", "rostov-na-donu"]]
    assert any("введите, что искать" in s for s in said)


def test_analyze_validates_url():
    url = "https://www.avito.ru/moskva/telefony/iphone_13_128_gb_1234567890"
    calls, _ = run_menu(["2", "не ссылка", url, "да", "", "5"])
    assert calls == [["analyze", url, "--open"]]


def test_watch_flow_and_telegram_hint():
    calls, said = run_menu(["3", "4", "rtx 3060", "25000", "", "10", "", "5"])
    assert calls == [["watch", "-c", "gpu", "-q", "rtx 3060", "--max-price", "25000", "--every", "10"]]
    assert any("Telegram" in s for s in said)


def test_watch_asks_telegram_when_configured():
    calls, _ = run_menu(["3", "1", "", "", "", "", "да", "", "5"], telegram={"token": "t", "chat_id": "1"})
    assert calls == [["watch", "-c", "phones", "--every", "15", "--telegram"]]


def test_eof_exits_cleanly():
    calls, said = run_menu([])
    assert calls == [] and said[-1] == "\nВыход."


def test_command_line_uses_launcher_name(monkeypatch):
    monkeypatch.setenv("AVITO_FLIP_CMD", "start.bat")
    assert command_line(["scan", "-c", "phones", "-q", "iphone 13"]) == 'start.bat scan -c phones -q "iphone 13"'
    monkeypatch.delenv("AVITO_FLIP_CMD")
    assert command_line(["demo"]) == "python -m avito_flip demo"


def test_main_without_args_opens_menu(monkeypatch):
    import avito_flip.cli as cli
    import avito_flip.interactive as interactive

    opened = []
    monkeypatch.setattr(interactive, "run_menu", lambda run, config=None: opened.append(run) or 0)
    monkeypatch.setattr(cli.sys, "argv", ["avito_flip"])
    assert cli.main() == 0 and opened == [cli.main]
