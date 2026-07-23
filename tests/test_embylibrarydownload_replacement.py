from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "replacement.py"
)
SPEC = spec_from_file_location("embylibrarydownload_replacement", MODULE_PATH)
replacement = module_from_spec(SPEC)
sys.modules[SPEC.name] = replacement
SPEC.loader.exec_module(replacement)


class FileItem(SimpleNamespace):
    pass


class HistoryOper:
    def __init__(self, history):
        self.history = history
        self.deleted = []

    def get_by_dest(self, path):
        return self.history if self.history and self.history.dest == path else None

    def delete(self, history_id):
        self.deleted.append(history_id)


class Storage:
    def __init__(self, existing, fail_path=None):
        self.existing = set(existing)
        self.fail_path = fail_path
        self.deleted = []

    def exists(self, item):
        return item.path in self.existing

    def delete_media_file(self, item):
        self.deleted.append(item.path)
        if item.path == self.fail_path:
            return False
        self.existing.discard(item.path)
        return True


def history():
    return SimpleNamespace(
        id=7,
        status=True,
        mode="softlink",
        download_hash="hash-7",
        src="/downloads/old.mkv",
        src_storage="local",
        src_fileitem={"path": "/downloads/old.mkv", "storage": "local", "type": "file"},
        dest="/library/old.mkv",
        dest_storage="local",
        dest_fileitem={"path": "/library/old.mkv", "storage": "local", "type": "file"},
    )


def test_cleanup_deletes_exact_link_source_and_history_in_order():
    histories = HistoryOper(history())
    storage = Storage({"/library/old.mkv", "/downloads/old.mkv"})

    success, error = replacement.cleanup_old_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem
    )

    assert success is True
    assert error == ""
    assert storage.deleted == ["/library/old.mkv", "/downloads/old.mkv"]
    assert histories.deleted == [7]


def test_new_version_requires_successful_history_and_existing_file():
    histories = HistoryOper(history())
    storage = Storage({"/library/old.mkv"})

    success, error = replacement.verify_new_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem
    )

    assert success is True
    assert error == ""


def test_new_version_verification_fails_without_file_and_never_deletes_old():
    histories = HistoryOper(history())
    storage = Storage(set())

    success, error = replacement.verify_new_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem
    )

    assert success is False
    assert "入库文件不存在" in error
    assert storage.deleted == []


def test_new_version_must_belong_to_current_download_task():
    histories = HistoryOper(history())
    storage = Storage({"/library/old.mkv"})

    success, error = replacement.verify_new_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem, "other-hash"
    )

    assert success is False
    assert "不属于当前下载任务" in error


def test_cleanup_keeps_transfer_record_when_source_deletion_fails():
    histories = HistoryOper(history())
    storage = Storage(
        {"/library/old.mkv", "/downloads/old.mkv"},
        fail_path="/downloads/old.mkv",
    )

    success, error = replacement.cleanup_old_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem
    )

    assert success is False
    assert "下载源文件删除失败" in error
    assert histories.deleted == []


def test_cleanup_refuses_to_guess_when_transfer_record_is_missing():
    histories = HistoryOper(None)
    storage = Storage({"/library/old.mkv", "/downloads/old.mkv"})

    success, error = replacement.cleanup_old_webdl_version(
        {"path": "/library/old.mkv"}, histories, storage, FileItem
    )

    assert success is False
    assert "未找到旧版本对应的转移记录" in error
    assert storage.deleted == []
