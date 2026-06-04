from core.models import ScrapedItem
from core.state import RunStore, content_hash


def test_change_detection_new_then_unchanged(tmp_path):
    s = RunStore("j", str(tmp_path))
    assert s.classify(ScrapedItem(url="u", data={"a": 1})) == "new"
    s.save(finished=True)

    s2 = RunStore("j", str(tmp_path))
    assert s2.classify(ScrapedItem(url="u", data={"a": 1})) == "unchanged"
    assert s2.classify(ScrapedItem(url="u", data={"a": 2})) == "new"
    assert s2.new == 1 and s2.unchanged == 1


def test_change_detection_is_per_record_not_per_url(tmp_path):
    # Two records sharing one URL must be tracked independently.
    s = RunStore("j", str(tmp_path))
    s.classify(ScrapedItem(url="u", data={"a": 1}))
    s.classify(ScrapedItem(url="u", data={"a": 2}))
    s.save(finished=True)
    s2 = RunStore("j", str(tmp_path))
    assert s2.classify(ScrapedItem(url="u", data={"a": 1})) == "unchanged"
    assert s2.classify(ScrapedItem(url="u", data={"a": 2})) == "unchanged"


def test_resume_roundtrip(tmp_path):
    s = RunStore("j", str(tmp_path))
    s.checkpoint(ScrapedItem(url="u1", data={"x": 1}, status_code=200))
    s.checkpoint(ScrapedItem(url="u2", data={"x": 2}, status_code=200))
    s.save(finished=False)        # simulate interruption

    s2 = RunStore("j", str(tmp_path))
    restored = s2.load_resume_items()
    assert [i.url for i in restored] == ["u1", "u2"]
    assert restored[0].data == {"x": 1}


def test_clean_finish_removes_sidecar(tmp_path):
    s = RunStore("j", str(tmp_path))
    s.checkpoint(ScrapedItem(url="u1", data={"x": 1}))
    s.save(finished=True)
    assert RunStore("j", str(tmp_path)).load_resume_items() == []


def test_content_hash_stable_and_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})
