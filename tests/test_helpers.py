import importlib

from core.models import JobConfig
from core.sitemap import _LOC_RE

scraper = importlib.import_module("scraper")


def test_parse_format_choice_numbers_names_all():
    p = scraper._parse_format_choice
    assert p("1,3") == ["csv", "json_pretty"]
    assert p("csv,pdf") == ["csv", "pdf"]
    assert p("excel md") == ["xlsx", "markdown"]   # aliases + space-separated
    assert "html" in p("all")
    assert p("nonsense") == []


def test_slug_from_url():
    assert scraper._slug_from_url("https://www.example.com/path") == "example"
    assert scraper._slug_from_url("https://books.toscrape.com") == "books"


def test_sitemap_loc_regex_strips_whitespace():
    xml = "<urlset><url><loc>https://e.com/a</loc></url><url><loc> https://e.com/b </loc></url></urlset>"
    assert _LOC_RE.findall(xml) == ["https://e.com/a", "https://e.com/b"]


def test_jobconfig_defaults():
    j = JobConfig(name="t", urls=["https://e.com"])
    assert j.mode == "auto"
    assert j.respect_robots is False
    assert j.from_sitemap is False
    assert j.track_changes is False
    assert j.resume is False
    assert j.capture is None
    assert j.export.formats == ["json", "terminal"]
