from core.autoschema import detect_fields, detect_schema

# Cards wrapped 3-per-row, each with a name, a decorative icon, and an info
# wrapper that contains the real capital/population fields.
PAGE = """
<html><body>
<div class="row">
  <div class="card"><h3 class="name"><i class="flag flag-a"></i>Alphaland Republic</h3>
    <div class="info"><span class="cap">Capital Alphacity</span><span class="pop">1000000</span></div></div>
  <div class="card"><h3 class="name"><i class="flag flag-b"></i>Betagua Kingdom</h3>
    <div class="info"><span class="cap">Capital Betatown</span><span class="pop">2000000</span></div></div>
</div>
<div class="row">
  <div class="card"><h3 class="name"><i class="flag flag-c"></i>Gammador State</h3>
    <div class="info"><span class="cap">Capital Gammaville</span><span class="pop">3000000</span></div></div>
</div>
</body></html>
"""


def test_detects_card_as_item():
    selector, rules = detect_schema(PAGE)
    assert selector == "div.card"
    names = {r.name for r in rules}
    assert "name" in names          # kept despite containing a decorative icon
    assert "cap" in names and "pop" in names
    assert "info" not in names      # wrapper containing real fields is dropped


def test_no_repeating_structure_returns_none():
    selector, rules = detect_schema("<html><body><p>just text</p></body></html>")
    assert selector is None
    assert rules == []


def test_detect_fields_for_explicit_selector():
    rules = detect_fields(PAGE, ".card")
    assert any(r.name == "name" for r in rules)
    assert all(r.selector for r in rules)
