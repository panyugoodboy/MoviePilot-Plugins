from enum import Enum
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import pytest


PLUGIN = Path(__file__).parents[1] / "plugins.v2" / "mporganizecorrect"


def load_service(monkeypatch):
    """用最小 MoviePilot 边界桩加载服务模块，并在用例结束后自动还原。"""

    class MediaType(Enum):
        MOVIE = "电影"
        TV = "电视剧"

    class FileItem:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    modules = {
        "app": ModuleType("app"),
        "app.chain": ModuleType("app.chain"),
        "app.chain.media": ModuleType("app.chain.media"),
        "app.chain.storage": ModuleType("app.chain.storage"),
        "app.chain.transfer": ModuleType("app.chain.transfer"),
        "app.core": ModuleType("app.core"),
        "app.core.config": ModuleType("app.core.config"),
        "app.core.metainfo": ModuleType("app.core.metainfo"),
        "app.db": ModuleType("app.db"),
        "app.db.transferhistory_oper": ModuleType("app.db.transferhistory_oper"),
        "app.helper": ModuleType("app.helper"),
        "app.helper.directory": ModuleType("app.helper.directory"),
        "app.log": ModuleType("app.log"),
        "app.schemas": ModuleType("app.schemas"),
        "app.schemas.types": ModuleType("app.schemas.types"),
    }
    modules["app.chain.media"].MediaChain = object
    modules["app.chain.storage"].StorageChain = object
    modules["app.chain.transfer"].TransferChain = object
    modules["app.core.config"].settings = SimpleNamespace(
        SCRAP_FOLLOW_TMDB=False,
        TMDB_IMAGE_URL=lambda path, size="original": (
            f"https://image.tmdb.org/t/p/{size}/{str(path).lstrip('/')}"
        ),
    )
    modules["app.core.metainfo"].MetaInfoPath = object
    modules["app.db.transferhistory_oper"].TransferHistoryOper = object
    modules["app.helper.directory"].DirectoryHelper = object
    modules["app.log"].logger = SimpleNamespace(error=lambda *args: None, debug=lambda *args: None)
    modules["app.schemas"].FileItem = FileItem
    modules["app.schemas.types"].MediaType = MediaType
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    package_name = "mporganizecorrect_service_test"
    package = ModuleType(package_name)
    package.__path__ = [str(PLUGIN)]
    monkeypatch.setitem(sys.modules, package_name, package)
    for child in ("matcher", "posters", "store", "service"):
        name = f"{package_name}.{child}"
        spec = spec_from_file_location(name, PLUGIN / f"{child}.py")
        module = module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.service"], MediaType, FileItem


class FakeStore:
    def __init__(self, record):
        self.record = record
        self.states = []
        self.audits = []

    def get_record(self, history_id):
        return self.record if history_id == self.record["history_id"] else None

    def set_state(self, history_id, state, reason="", candidate=None, updated_at=""):
        self.states.append((history_id, state, reason))

    def add_audit(self, item):
        self.audits.append(item)
        return len(self.audits)


def build_history(source, old_dest, *, history_id=7):
    return SimpleNamespace(
        id=history_id,
        src=str(source),
        src_storage="local",
        src_fileitem={"path": str(source), "storage": "local", "type": "file", "extension": "mkv"},
        dest=str(old_dest),
        dest_storage="local",
        dest_fileitem={"path": str(old_dest), "storage": "local", "type": "file", "extension": "mkv"},
        mode="move",
        type="电影",
        category="电影",
        title="The Wandering Earth",
        year="2019",
        tmdbid=1,
        imdbid="",
        tvdbid=None,
        doubanid="",
        bangumiid=None,
        anilistid=None,
        media_source="themoviedb",
        media_id="1",
        seasons="",
        episodes="",
        image="",
        downloader="qbittorrent",
        download_hash="hash",
        status=True,
        errmsg="",
        date="2026-08-10 10:00:00",
        files=[],
        episode_group="",
    )


def test_scan_skips_history_whose_destination_media_directory_is_chinese(monkeypatch):
    service_module, _, _ = load_service(monkeypatch)
    history = build_history(
        "/downloads/[求药].Searching.For.Medicine.2026.mkv",
        "/LINK/电影/华语电影/求药 (2026)/求药 (2026)-2160p.mkv",
    )
    history.title = "Searching For Medicine"
    history.year = "2026"
    service = service_module.OrganizeCorrectService(object(), lambda: {})

    assert service._analyze_history(history) is None


def test_successful_correction_deletes_old_target_only_after_new_output_exists(tmp_path, monkeypatch):
    service_module, media_type, _ = load_service(monkeypatch)
    source = tmp_path / "流浪地球.2019.mkv"
    old_dest = tmp_path / "library" / "The Wandering Earth (2019)" / "movie.mkv"
    new_dest = tmp_path / "library" / "流浪地球 (2019)" / "movie.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    old_dest.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    old_dest.write_bytes(b"old")
    old_history = build_history(source, old_dest)
    new_history = build_history(source, new_dest, history_id=8)
    new_history.title = "流浪地球"
    new_history.tmdbid = 535167
    new_history.media_id = "535167"
    events = []

    class FakeOper:
        current = old_history

        def get(self, history_id):
            return self.current if self.current and self.current.id == history_id else None

        def get_by_src(self, path, storage=None):
            return self.current if self.current and self.current.src == path else None

        def delete(self, history_id):
            events.append("delete_history")
            self.current = None
            FakeOper.current = None

        def add(self, **snapshot):
            events.append("restore_history")
            FakeOper.current = SimpleNamespace(**snapshot)

    class FakeTransfer:
        def transfer(self, **kwargs):
            assert kwargs["preview"] is True
            assert kwargs["transfer_type"] == "copy"
            return SimpleNamespace(
                success=True,
                message="",
                target_item=SimpleNamespace(storage="local", path=str(new_dest)),
            )

        def do_transfer(self, **kwargs):
            events.append("do_transfer")
            assert kwargs["transfer_type"] == "copy"
            assert source.exists()
            new_dest.parent.mkdir(parents=True, exist_ok=True)
            new_dest.write_bytes(b"new")
            FakeOper.current = new_history
            return True, ""

    class FakeStorage:
        def delete_media_file(self, item):
            events.append("delete_media")
            assert item.path == str(old_dest)
            assert source.exists()
            assert new_dest.exists()
            old_dest.unlink()
            return True

    class FakeMediaChain:
        def recognize_media(self, **kwargs):
            return SimpleNamespace(title="English", year="2019", type=media_type.MOVIE)

    monkeypatch.setattr(service_module, "TransferHistoryOper", FakeOper)
    monkeypatch.setattr(service_module, "TransferChain", FakeTransfer)
    monkeypatch.setattr(service_module, "StorageChain", FakeStorage)
    monkeypatch.setattr(service_module, "MediaChain", FakeMediaChain)
    monkeypatch.setattr(service_module, "DirectoryHelper", lambda: SimpleNamespace(get_dir=lambda **kwargs: object()))
    monkeypatch.setattr(service_module, "MetaInfoPath", lambda path: SimpleNamespace(begin_season=None))

    snapshot = service_module.OrganizeCorrectService._history_snapshot(old_history)
    record = {
        "history_id": 7,
        "src": str(source),
        "old_dest": str(old_dest),
        "old_title": old_history.title,
        "snapshot": snapshot,
    }
    store = FakeStore(record)
    service = service_module.OrganizeCorrectService(store, lambda: {})
    candidate = {
        "title": "流浪地球",
        "year": 2019,
        "media_type": "电影",
        "media_source": "themoviedb",
        "media_id": "535167",
    }

    result = service._correct_one(record, candidate, cleanup_old=True)

    assert result["success"] is True
    assert events == ["delete_history", "do_transfer", "delete_media"]
    assert source.exists()
    assert new_dest.exists()
    assert not old_dest.exists()
    assert store.states[-1][1] == "corrected"
    assert service_module.settings.SCRAP_FOLLOW_TMDB is False


def test_failed_correction_restores_record_and_keeps_source_and_old_media(tmp_path, monkeypatch):
    service_module, media_type, _ = load_service(monkeypatch)
    source = tmp_path / "流浪地球.2019.mkv"
    old_dest = tmp_path / "library" / "The Wandering Earth (2019)" / "movie.mkv"
    new_dest = tmp_path / "library" / "流浪地球 (2019)" / "movie.mkv"
    old_dest.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    old_dest.write_bytes(b"old")
    old_history = build_history(source, old_dest)
    events = []

    class FakeOper:
        current = old_history

        def get(self, history_id):
            return self.current if self.current and self.current.id == history_id else None

        def get_by_src(self, path, storage=None):
            return self.current

        def delete(self, history_id):
            events.append("delete_history")
            FakeOper.current = None

        def add(self, **snapshot):
            events.append("restore_history")
            FakeOper.current = SimpleNamespace(**snapshot)

    class FakeTransfer:
        def transfer(self, **kwargs):
            return SimpleNamespace(
                success=True,
                message="",
                target_item=SimpleNamespace(storage="local", path=str(new_dest)),
            )

        def do_transfer(self, **kwargs):
            events.append("do_transfer")
            return False, "模拟整理失败"

    class FakeMediaChain:
        def recognize_media(self, **kwargs):
            return SimpleNamespace(title="English", year="2019", type=media_type.MOVIE)

    class FakeStorage:
        def delete_media_file(self, item):
            events.append("delete_media")
            return True

    monkeypatch.setattr(service_module, "TransferHistoryOper", FakeOper)
    monkeypatch.setattr(service_module, "TransferChain", FakeTransfer)
    monkeypatch.setattr(service_module, "StorageChain", FakeStorage)
    monkeypatch.setattr(service_module, "MediaChain", FakeMediaChain)
    monkeypatch.setattr(service_module, "DirectoryHelper", lambda: SimpleNamespace(get_dir=lambda **kwargs: object()))
    monkeypatch.setattr(service_module, "MetaInfoPath", lambda path: SimpleNamespace(begin_season=None))

    snapshot = service_module.OrganizeCorrectService._history_snapshot(old_history)
    record = {
        "history_id": 7,
        "src": str(source),
        "old_dest": str(old_dest),
        "old_title": old_history.title,
        "snapshot": snapshot,
    }
    service = service_module.OrganizeCorrectService(FakeStore(record), lambda: {})
    candidate = {
        "title": "流浪地球",
        "year": 2019,
        "media_type": "电影",
        "media_source": "themoviedb",
        "media_id": "535167",
    }

    with pytest.raises(RuntimeError, match="模拟整理失败"):
        service._correct_one(record, candidate, cleanup_old=True)

    assert events == ["delete_history", "do_transfer", "restore_history"]
    assert source.exists()
    assert old_dest.exists()
    assert not new_dest.exists()
    assert service_module.settings.SCRAP_FOLLOW_TMDB is False


def test_correct_all_ready_processes_every_ready_record(monkeypatch):
    service_module, _, _ = load_service(monkeypatch)

    class ReadyStore:
        requested_limit = "unset"

        def list_ready(self, limit=None):
            self.requested_limit = limit
            return [
                {"history_id": history_id, "candidate": {"media_id": str(history_id)}}
                for history_id in range(1, 76)
            ]

    store = ReadyStore()
    service = service_module.OrganizeCorrectService(store, lambda: {})
    captured = {}

    def fake_correct(items, **kwargs):
        captured.update(items=list(items), **kwargs)
        return {"total": len(captured["items"]), "success": len(captured["items"]), "failed": 0}

    monkeypatch.setattr(service, "_correct_records", fake_correct)
    result = service.correct_all_ready(cleanup_old=True)

    assert store.requested_limit is None
    assert result["total"] == 75
    assert captured["automatic"] is True
    assert captured["cleanup_old"] is True


def test_reset_and_scan_clears_only_plugin_records_then_runs_full_scan(monkeypatch):
    service_module, _, _ = load_service(monkeypatch)
    events = []

    class ResetStore:
        def clear_records(self):
            events.append("clear")
            return 12

    service = service_module.OrganizeCorrectService(ResetStore(), lambda: {})

    def fake_scan(*, full, progress):
        events.append(("scan", full))
        progress({"current": 1, "total": 1, "message": "扫描完成"})
        return {"checked": 8, "listed": 3, "ready": 2, "manual": 1, "failed": 0}

    monkeypatch.setattr(service, "_scan", fake_scan)
    progress = []

    result = service.reset_and_scan(progress=progress.append)

    assert events == ["clear", ("scan", True)]
    assert result == {
        "cleared": 12,
        "checked": 8,
        "listed": 3,
        "ready": 2,
        "manual": 1,
        "failed": 0,
    }
    assert progress[0]["message"] == "正在清除插件纠正记录"


def test_reset_and_scan_refuses_to_clear_during_another_operation(monkeypatch):
    service_module, _, _ = load_service(monkeypatch)

    class ResetStore:
        cleared = False

        def clear_records(self):
            self.cleared = True
            return 1

    store = ResetStore()
    service = service_module.OrganizeCorrectService(store, lambda: {})
    service._operation_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="已有扫描、纠正或清理任务正在运行"):
            service.reset_and_scan()
    finally:
        service._operation_lock.release()

    assert store.cleared is False


def test_manual_search_accepts_english_title_without_year_and_uses_fuzzy_query(monkeypatch):
    service_module, media_type, _ = load_service(monkeypatch)
    calls = []

    class SearchMediaChain:
        def search(self, query):
            calls.append(query)
            return None, [SimpleNamespace(
                title="流浪地球",
                original_title="The Wandering Earth",
                names=[],
                year=2019,
                type=media_type.MOVIE,
                source="themoviedb",
                media_id="535167",
                tmdb_id=535167,
                douban_id=None,
                bangumi_id=None,
                anilist_id=None,
                poster_path="/wandering-earth.jpg",
            )]

    monkeypatch.setattr(service_module, "MediaChain", SearchMediaChain)
    store = FakeStore({"history_id": 7})
    service = service_module.OrganizeCorrectService(store, lambda: {})

    results = service.search_record(
        7,
        title="The Wandering Earth",
        year=0,
        media_type="电影",
    )

    assert calls == ["The Wandering Earth"]
    assert results[0]["title"] == "流浪地球"
    assert results[0]["poster_url"] == (
        "https://image.tmdb.org/t/p/w500/wandering-earth.jpg"
    )


def test_correction_progress_displays_source_filename(monkeypatch):
    service_module, _, _ = load_service(monkeypatch)
    record = {
        "history_id": 7,
        "src": r"D:\Downloads\The.Wandering.Earth.2019.mkv",
        "state": "ready",
        "media_type": "电影",
        "candidate": {"media_id": "535167"},
    }
    service = service_module.OrganizeCorrectService(FakeStore(record), lambda: {})
    monkeypatch.setattr(
        service,
        "_correct_one",
        lambda item, candidate, cleanup_old: {
            "history_id": item["history_id"],
            "success": True,
        },
    )
    progress = []

    result = service.correct_records(
        [{"history_id": 7}],
        cleanup_old=False,
        progress=progress.append,
    )

    assert result["success"] == 1
    assert "The.Wandering.Earth.2019.mkv" in progress[-1]["message"]


def test_search_retries_without_year_after_year_query_misses(monkeypatch):
    service_module, media_type, _ = load_service(monkeypatch)
    calls = []

    class SearchMediaChain:
        def search(self, query):
            calls.append(query)
            if len(calls) == 1:
                return None, []
            return None, [SimpleNamespace(
                title="流浪地球",
                original_title="The Wandering Earth",
                names=[],
                year=2019,
                type=media_type.MOVIE,
                source="themoviedb",
                media_id="535167",
                tmdb_id=535167,
                douban_id=None,
                bangumi_id=None,
                anilist_id=None,
                poster_path="",
            )]

    monkeypatch.setattr(service_module, "MediaChain", SearchMediaChain)
    service = service_module.OrganizeCorrectService(FakeStore({"history_id": 7}), lambda: {})

    results = service.search_record(
        7,
        title="The Wandering Earth",
        year=2019,
        media_type="电影",
    )

    assert calls == ["The Wandering Earth 2019", "The Wandering Earth"]
    assert results[0]["media_id"] == "535167"
