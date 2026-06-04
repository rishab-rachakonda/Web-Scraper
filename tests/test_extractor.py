from core.extractor import DataExtractor, _auto_cast
from core.models import ExtractorRule

HTML = """
<div class="item"><h2 class="title">Hello</h2><span class="price">£12.50</span>
  <a class="lnk" href="/x">go</a></div>
<div class="item"><h2 class="title">World</h2><span class="price">£3.00</span>
  <a class="lnk" href="/y">go</a></div>
"""


def test_extract_items_one_record_each():
    rules = [
        ExtractorRule(name="title", selector=".title"),
        ExtractorRule(name="price", selector=".price", transform="auto"),
    ]
    items = DataExtractor(rules).extract_items(HTML, ".item")
    assert len(items) == 2
    assert items[0] == {"title": "Hello", "price": 12.5}
    assert items[1]["title"] == "World"
    assert items[1]["price"] == 3.0


def test_attribute_absolutised():
    rules = [ExtractorRule(name="link", selector=".lnk", attribute="href")]
    items = DataExtractor(rules).extract_items(HTML, ".item", base_url="https://e.com/a")
    assert items[0]["link"] == "https://e.com/x"


def test_multiple_flag_returns_list():
    rules = [ExtractorRule(name="titles", selector=".title", multiple=True)]
    out = DataExtractor(rules).extract_html(HTML)
    assert out["titles"] == ["Hello", "World"]


def test_extract_json_jsonpath():
    ex = DataExtractor([ExtractorRule(name="n", jsonpath="user.name")])
    assert ex.extract_json({"user": {"name": "Ada"}})["n"] == "Ada"


def test_auto_cast_numbers_and_dates():
    assert _auto_cast("84000") == 84000
    assert _auto_cast("£51.77") == 51.77
    assert _auto_cast("1,246,700") == 1246700
    assert _auto_cast("29800.0") == 29800.0
    assert _auto_cast("2024-01-15") == "2024-01-15"
    assert _auto_cast("13 Dec 1946") == "1946-12-13"
    assert _auto_cast("Andorra") == "Andorra"
    assert _auto_cast("") == ""


def test_named_transforms():
    rules = [ExtractorRule(name="t", selector=".title", transform="upper")]
    assert DataExtractor(rules).extract_items(HTML, ".item")[0]["t"] == "HELLO"
