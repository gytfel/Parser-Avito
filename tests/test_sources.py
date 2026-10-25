from pathlib import Path

from avito_flip.sources.avito import AvitoSource, build_search_url, parse_cards, parse_item_page, parse_search_page
from avito_flip.sources.csv_file import CsvSource
from avito_flip.sources.youla import parse_search_page as parse_youla

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_build_search_url():
    assert build_search_url("moskva", "telefony", "iphone 13", page=2, sort="date", price_max=40000) == (
        "https://www.avito.ru/moskva/telefony?q=iphone+13&s=104&pmax=40000&p=2"
    )
    assert build_search_url("all", "", "ps5", sort="default") == "https://www.avito.ru/all?q=ps5"


def test_avito_state_json():
    items = {x.id: x for x in parse_search_page(read("avito_search_state.html"))}
    assert set(items) == {"3456789012", "3456789013", "3456789014"}  # блок «Похожие категории» без цены отброшен
    a = items["3456789012"]
    assert a.title == "iPhone 13, 128 ГБ" and a.price == 38000
    assert a.url == "https://www.avito.ru/moskva/telefony/iphone_13_128_gb_3456789012"
    assert a.location == "Москва" and a.images == 2 and a.published is not None
    # Описание из HTML-карточки длиннее, чем в JSON — берём полное
    assert "Торг уместен" in a.description
    assert items["3456789014"].location == "Москва, Тверская ул."


def test_avito_initial_data_urlencoded():
    (x,) = parse_search_page(read("avito_search_initial.html"))
    assert (x.id, x.title, x.price, x.location) == ("777000111", "PlayStation 5 с дисководом", 41000, "Санкт-Петербург")


def test_avito_html_cards_fallback():
    cards = {x.id: x for x in parse_cards(read("avito_search_cards.html"))}
    assert set(cards) == {"4100000001", "4100000002"}  # карточка без цены пропущена
    a, b = cards["4100000001"], cards["4100000002"]
    assert a.title == "Видеокарта Palit RTX 3060 Ti 8GB" and a.price == 23500
    assert a.description.startswith("Не майнила") and a.location == "Москва, м. Таганская"
    assert b.title == "RTX 3060 Ti Gigabyte 8 ГБ" and b.price == 19000 and "майнинга" in b.description


def test_avito_item_page():
    info = parse_item_page(read("avito_item.html"))
    assert info["title"] == "iPhone 13 Pro, 256 ГБ" and info["price"] == 52000
    assert info["params"]["Встроенная память"] == "256 ГБ" and info["params"]["Состояние"] == "Б/у"
    assert "трещина" in info["description"] and info["seller"] == "Алексей"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, referer=None):
        self.urls.append(url)
        return self.pages.get(url, "<html></html>")


def test_avito_source_paginates_and_details():
    url1 = build_search_url("moskva", "telefony", "iphone 13", 1)
    item_url = "https://www.avito.ru/moskva/telefony/iphone_13_128_gb_3456789012"
    fetcher = FakeFetcher({url1: read("avito_search_state.html"), item_url: read("avito_item.html")})
    src = AvitoSource(fetcher, "moskva")
    found = src.search("iphone 13", path="telefony", pages=3)
    assert len(found) == 3
    assert len(fetcher.urls) == 2  # вторая страница пустая — дальше не идём
    x = next(f for f in found if f.id == "3456789012")
    src.fetch_details(x)
    assert x.detailed and x.params["Встроенная память"] == "256 ГБ" and x.seller == "Алексей"


def test_youla_state_with_kopecks():
    items = {x.title: x for x in parse_youla(read("youla_search.html"))}
    assert items["iPhone 13 128gb"].price == 37000
    assert items["Айфон 13 256 гб"].price == 43000
    assert items["iPhone 13 128gb"].url.startswith("https://youla.ru/moskva/elektronika/telefony/")
    assert items["iPhone 13 128gb"].location == "Москва, Арбат"


def test_youla_cards_fallback():
    items = {x.title: x for x in parse_youla(read("youla_cards.html"))}
    assert set(items) == {"Sony PlayStation 5", "PS5 Slim Digital"}
    assert items["Sony PlayStation 5"].price == 39990


def test_csv_source(tmp_path):
    f = tmp_path / "ozon.csv"
    f.write_text("название;цена;площадка;состояние\niPhone 13 128 ГБ;39 990 ₽;ozon;Б/у\nЧехол;500;ozon;\n"
                 "iPhone 12;20000;ozon;\n", encoding="utf-8")
    found = CsvSource([str(f)]).search("iphone 13")
    assert len(found) == 1 and found[0].source == "ozon" and found[0].price == 39990
    assert found[0].params == {"Состояние": "Б/у"}
