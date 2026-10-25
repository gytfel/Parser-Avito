import pytest

from avito_flip.specs import extract_specs
from avito_flip.text import normalize, parse_price, query_matches


def test_normalize_transliterates_brands_and_units():
    assert normalize("Айфон 13 Про Макс 256ГБ") == "iphone 13 pro max 256 gb"
    assert normalize("Плойка 5 slim, 1ТБ") == "ps5 slim, 1 tb"
    assert normalize("Samsung A54 5G") == "samsung a54 5g"  # 5G — не память


@pytest.mark.parametrize("value, expected", [("38 000 ₽", 38000), ("38000", 38000), (41000.0, 41000), ("", None),
                                             (None, None), ("договорная", None)])
def test_parse_price(value, expected):
    assert parse_price(value) == expected


def test_query_matches_with_transliteration():
    assert query_matches("iphone 13", "Айфон 13, 128 ГБ")
    assert query_matches("iphone 13 128gb", "iPhone 13 128 ГБ")
    assert not query_matches("iphone 13", "iPhone 12, 128 ГБ")


@pytest.mark.parametrize("title, profile, key", [
    ("iPhone 13, 128 ГБ", "phones", "iphone 13 | 128gb"),
    ("Айфон 13 про макс 256гб", "phones", "iphone 13 pro max | 256gb"),
    ("iPhone 12/128", "phones", "iphone 12 | 128gb"),
    ("iPhone 16e 128", "phones", "iphone 16e | 128gb"),
    ("iPhone SE 2020 64gb", "phones", "iphone se | 64gb | 2"),
    ("Xiaomi Redmi Note 12 Pro 8/256 ГБ", "phones", "redmi note 12 pro | 256gb | 8gb ram"),
    ("Samsung Galaxy S23 Ultra 12/512", "phones", "galaxy s23 ultra | 512gb | 12gb ram"),
    ("MacBook Air 13 M1 8/256", "laptops", 'macbook air | 256gb | 8gb ram | m1 | 13"'),
    ("Asus TUF Gaming F15 i5-11400H RTX 3050 16gb ram 512 ssd", "laptops",
     "asus tuf f15 | 512gb | 16gb ram | i5-11400h | rtx 3050"),
    ("Видеокарта Palit RTX 3060 Ti 8GB", "gpu", "rtx 3060 ti | 8gb"),
    ("Sony PlayStation 5 с дисководом", "consoles", "ps5 | disc"),
    ("PS5 Slim Digital 1TB", "consoles", "ps5 slim | 1tb | digital"),
    ("AirPods Pro 2", "audio", "airpods pro 2"),
    ("Apple Watch Series 8 45mm", "watches", "apple watch 8 | 45mm"),
    ("Nintendo Switch OLED", "generic", "switch oled"),
])
def test_model_keys(title, profile, key):
    assert extract_specs(title, profile=profile).key == key


def test_params_and_battery_from_listing_page():
    s = extract_specs("iPhone 13 Pro", "Аккумулятор 85%, Face ID работает",
                      {"Встроенная память": "256 ГБ", "Модель": "iPhone 13 Pro"}, "phones")
    assert (s.model, s.storage_gb, s.battery) == ("iphone 13 pro", 256, 85)
    assert extract_specs("iPhone 13", "износ батареи 12%", None, "phones").battery == 88


def test_similarity_requires_same_model_and_memory():
    a = extract_specs("iPhone 13, 128 ГБ", profile="phones")
    assert a.similarity(extract_specs("Айфон 13 128гб", profile="phones")) == 1.0
    assert a.similarity(extract_specs("iPhone 13 Pro, 128 ГБ", profile="phones")) == 0.0
    assert a.similarity(extract_specs("iPhone 13, 256 ГБ", profile="phones")) < 0.7
    assert 0.7 <= a.similarity(extract_specs("iPhone 13", profile="phones")) < 1.0
