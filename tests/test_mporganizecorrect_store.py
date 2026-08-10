from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "plugins.v2" / "mporganizecorrect" / "store.py"
SPEC = spec_from_file_location("mporganizecorrect_store", MODULE_PATH)
store_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = store_module
SPEC.loader.exec_module(store_module)


def sample_record(history_id=10):
    return {
        "history_id": history_id,
        "media_type": "电影",
        "old_title": "The Wandering Earth",
        "old_year": 2019,
        "src": "D:/Downloads/流浪地球.2019.mkv",
        "old_dest": "D:/Library/The Wandering Earth (2019)/movie.mkv",
        "query_title": "流浪地球",
        "query_year": 2019,
        "mode": "link",
        "state": "ready",
        "reason": "唯一精确匹配",
        "candidate": {"title": "流浪地球", "media_id": "535167"},
        "options": [{"title": "流浪地球", "media_id": "535167"}],
        "snapshot": {"id": history_id, "src_storage": "local", "dest_storage": "local"},
        "created_at": "2026-08-10T10:00:00+08:00",
        "updated_at": "2026-08-10T10:00:00+08:00",
    }


def test_store_round_trips_record_and_supports_filters(tmp_path):
    store = store_module.CorrectionStore(tmp_path / "correct.db")
    store.upsert_record(sample_record())

    record = store.get_record(10)
    listed = store.list_records(state="ready", keyword="流浪地球", media_type="电影")

    assert record["candidate"]["media_id"] == "535167"
    assert record["snapshot"]["src_storage"] == "local"
    assert listed["total"] == 1


def test_ignored_record_stays_ignored_during_rescan(tmp_path):
    store = store_module.CorrectionStore(tmp_path / "correct.db")
    store.upsert_record(sample_record())
    assert store.set_ignored([10], True, "2026-08-10T10:01:00+08:00") == 1

    changed = sample_record()
    changed.update({"state": "failed", "reason": "new scan error", "updated_at": "2026-08-10T10:02:00+08:00"})
    store.upsert_record(changed)

    assert store.get_record(10)["ignored"] is True
    assert store.list_records(state="ignored")["total"] == 1
    assert store.stats()["ignored"] == 1


def test_store_records_cleanup_state_and_audit(tmp_path):
    store = store_module.CorrectionStore(tmp_path / "correct.db")
    store.upsert_record(sample_record())
    store.set_state(10, "cleanup_pending", "旧媒体删除失败", updated_at="2026-08-10T10:03:00+08:00")
    audit_id = store.add_audit({
        "action": "correct",
        "history_id": 10,
        "old_title": "The Wandering Earth",
        "new_title": "流浪地球",
        "src": "D:/Downloads/流浪地球.2019.mkv",
        "old_dest": "old.mkv",
        "new_dest": "new.mkv",
        "media_source": "themoviedb",
        "media_id": "535167",
        "status": "cleanup_pending",
        "message": "旧媒体删除失败",
        "created_at": "2026-08-10T10:03:00+08:00",
    })

    assert audit_id == 1
    assert store.stats()["cleanup_pending"] == 1
    assert store.list_audits()["items"][0]["new_title"] == "流浪地球"


def test_list_ready_has_no_limit_unless_explicitly_requested(tmp_path):
    store = store_module.CorrectionStore(tmp_path / "correct.db")
    for history_id in range(1, 76):
        store.upsert_record(sample_record(history_id))

    assert len(store.list_ready()) == 75
    assert len(store.list_ready(5)) == 5


def test_clear_records_keeps_audits_and_resets_scan_cursor(tmp_path):
    store = store_module.CorrectionStore(tmp_path / "correct.db")
    store.upsert_record(sample_record())
    store.add_audit({"action": "correct", "history_id": 10})
    store.set_meta("last_scan_date", "2026-08-10 10:00:00")
    store.set_meta("last_scan_at", "2026-08-10T10:00:00+08:00")

    cleared = store.clear_records()

    assert cleared == 1
    assert store.stats()["total"] == 0
    assert store.list_audits()["total"] == 1
    assert store.get_meta("last_scan_date", "") == ""
    assert store.get_meta("last_scan_at", "") == ""
