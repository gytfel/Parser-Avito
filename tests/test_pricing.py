import pytest

from avito_flip.analysis import analyze
from avito_flip.config import Economics
from avito_flip.models import Listing
from avito_flip.pricing import evaluate, find_market, market_overview, weighted_quantile


def item(i, title, price, desc="", source="avito", detailed=True):
    return analyze(Listing(source=source, id=str(i), title=title, price=price, description=desc,
                           url=f"https://www.avito.ru/x_{i}", detailed=detailed), "phones")


def market_pool():
    prices = [38000, 39000, 40000, 40500, 41000, 42000, 43000, 44000]
    pool = [item(i, "iPhone 13, 128 ГБ", p, "Хорошее состояние, всё работает") for i, p in enumerate(prices)]
    pool += [
        item(100, "iPhone 13, 128 ГБ", 9000, "На запчасти"),  # не аналог
        item(101, "iPhone 13, 128 ГБ", 120000, "Хорошее состояние"),  # выброс
        item(102, "iPhone 13, 256 ГБ", 47000, "Хорошее"),  # другая память
        item(103, "iPhone 13 Pro, 128 ГБ", 52000, "Хорошее"),  # другая модель
        item(104, "iPhone 13 128gb", 12000, "Реплика 1:1"),  # подделка
    ]
    return pool


def test_weighted_quantile_matches_plain_median():
    assert weighted_quantile([1, 2, 3], [1, 1, 1], 0.5) == 2
    assert weighted_quantile([10, 20], [1, 3], 0.5) > 15  # вес тянет к 20
    with pytest.raises(ValueError):
        weighted_quantile([], [], 0.5)


def test_market_uses_only_clean_comparables_without_outliers():
    pool = market_pool()
    target = item(200, "iPhone 13, 128 ГБ", 30000, "Хорошее")
    m = find_market(target, pool, Economics())
    assert m is not None
    assert m.n == 8
    assert {c.price for c in m.comparables} == {38000, 39000, 40000, 40500, 41000, 42000, 43000, 44000}
    assert 40000 <= m.median <= 41000


def test_cheap_clean_listing_is_good_deal():
    econ = Economics()
    target = item(200, "iPhone 13, 128 ГБ", 31000, "Отличное состояние, АКБ 90%. Торг")
    deal = evaluate(target, find_market(target, market_pool(), econ), econ)
    assert deal.verdict == "good"
    assert deal.profit > econ.min_profit
    assert deal.buy_price == pytest.approx(31000 * (1 - econ.buy_haggle))
    assert deal.sell_price < 41000  # продаём чуть ниже медианы с уступкой


def test_expensive_listing_is_bad_deal():
    econ = Economics()
    target = item(200, "iPhone 13, 128 ГБ", 44000, "Хорошее")
    assert evaluate(target, find_market(target, market_pool(), econ), econ).verdict == "bad"


def test_repair_strategy_for_broken_screen():
    econ = Economics()
    target = item(200, "iPhone 13, 128 ГБ", 15000, "Разбит экран, остальное работает")
    deal = evaluate(target, find_market(target, market_pool(), econ), econ)
    assert deal.repair_cost > 0
    assert deal.strategy.startswith("Починить")
    assert deal.profit is not None and deal.profit > 0


def test_suspiciously_cheap_listing_gets_risk():
    econ = Economics()
    target = item(200, "iPhone 13, 128 ГБ", 15000, "Отличное состояние")
    deal = evaluate(target, find_market(target, market_pool(), econ), econ)
    assert any("ниже рынка" in r for r in deal.risks)
    assert deal.verdict == "risky"


def test_fake_and_no_market():
    econ = Economics()
    fake = item(200, "iPhone 13, 128 ГБ", 12000, "Копия, реплика")
    assert evaluate(fake, find_market(fake, market_pool(), econ), econ).verdict == "fake"
    lonely = item(201, "Samsung Galaxy S8, 64 ГБ", 5000)
    deal = evaluate(lonely, find_market(lonely, market_pool(), econ), econ)
    assert deal.verdict == "nodata" and deal.profit is None


def test_battery_below_market_lowers_price():
    econ = Economics()
    pool = market_pool()
    good = item(200, "iPhone 13, 128 ГБ", 30000, "АКБ 95%")
    worn = item(201, "iPhone 13, 128 ГБ", 30000, "АКБ 78%")
    p_good = evaluate(good, find_market(good, pool, econ), econ).sell_price
    p_worn = evaluate(worn, find_market(worn, pool, econ), econ).sell_price
    assert p_worn < p_good


def test_market_overview_groups_by_model():
    rows = {r["key"]: r for r in market_overview(market_pool())}
    assert rows["iphone 13 | 128gb"]["n"] == 8  # без запчастей, подделки и выброса
