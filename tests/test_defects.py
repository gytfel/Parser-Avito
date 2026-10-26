import pytest

from avito_flip.defects import analyze_defects


def codes(text, profile="phones"):
    return {d.code for d in analyze_defects(text, profile).defects}


def bonuses(text):
    return {b.code for b in analyze_defects(text).bonuses}


@pytest.mark.parametrize("text, expected", [
    ("Разбит экран, все остальное работает", {"screen"}),
    ("На экране трещина в углу", {"screen"}),
    ("Трещина на задней крышке", {"back_cover"}),
    ("Мелкие царапины на корпусе, без коробки", {"scratches"}),
    ("Без коробки, царапины на корпусе", {"scratches"}),
    ("Не работает Face ID, в остальном отлично", {"component:Face ID / Touch ID"}),
    ("работает всё, кроме NFC", {"component:связь (Wi-Fi / BT / NFC / модем)"}),
    ("Аккумулятор под замену, быстро садится", {"battery_bad"}),
    ("Заблокирован на iCloud, продаю как есть", {"lock"}),
    ("Сколько стоит? скол на углу", {"chips"}),
])
def test_defects_found(text, expected):
    assert codes(text) == expected


@pytest.mark.parametrize("text", [
    "Состояние отличное, без сколов и царапин, коробка, чек",
    "Царапин нет, АКБ 88%. Торг уместен",
    "Экран не разбит, не вскрывался, не ремонтировался",
    "На защитном стекле трещина, сам экран целый",
    "Без сколов, царапин и потертостей",
    "iCloud чистый, отвязан от аккаунта",
    "в ремонте не был, дефектов нет",
    "Все функции работают, ничего не сломано, без нареканий",
])
def test_negated_phrases_are_not_defects(text):
    assert codes(text) == set()


def test_gpu_specific_rules():
    assert codes("Видеокарта не майнила, без артефактов", "gpu") == set()
    assert codes("После майнинга, артефакты в играх", "gpu") == {"artifacts", "mining"}
    # «артефакты» у телефона — не правило видеокарт
    assert "artifacts" not in codes("артефакты на экране", "phones")


def test_parts_and_discount_cap():
    r = analyze_defects("Продаю на запчасти, не включается")
    assert r.is_parts and r.level == 3
    assert r.discount == pytest.approx(0.85)
    assert not r.repairable


def test_scaling_by_intensity():
    soft = analyze_defects("мелкие царапины").defects[0].discount
    hard = analyze_defects("сильные царапины").defects[0].discount
    assert soft < 0.04 < hard


def test_bonuses_respect_negation_and_commas():
    assert bonuses("Состояние отличное, без сколов и царапин, коробка, чек") == {"box", "receipt", "ideal"}
    assert bonuses("без коробки и документов, полный комплект аксессуаров") == {"box"}
    assert bonuses("Гарантия закончилась") == set()
    assert "warranty" in bonuses("не майнила, без артефактов, гарантия до 2027")


def test_risks_fake_and_prepayment():
    r = analyze_defects("Реплика 1:1, качество отличное")
    assert r.is_fake and r.risks
    assert not analyze_defects("Не реплика, оригинал. Копия чека есть").is_fake
    assert analyze_defects("Только предоплата, отправлю СДЭКом").risks
    assert not analyze_defects("Без предоплаты, встреча лично").risks


def test_haggle_and_sealed():
    assert analyze_defects("Торг уместен").haggle is True
    assert analyze_defects("Цена окончательная, без торга").haggle is False
    assert analyze_defects("Запечатан, новый").is_new
    assert not analyze_defects("не вскрывался, в ремонте не был").is_new


def test_condition_param():
    assert analyze_defects("", condition="Требует ремонта").is_parts
    assert analyze_defects("", condition="Новое").is_new
    assert {d.code for d in analyze_defects("", condition="Удовлетворительное").defects} == {"condition_fair"}
