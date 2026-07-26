from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "qb_repair.py"
)
SPEC = spec_from_file_location("embylibrarydownload_qb_repair", MODULE_PATH)
module = module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
build_qb_path_repair_plan = module.build_qb_path_repair_plan


def test_repair_plan_only_includes_active_jobs_still_using_temp_path():
    torrents = [
        {
            "hash": "broken",
            "name": "Broken Movie",
            "content_path": "/临时/Broken Movie.mkv",
            "download_path": "/临时/",
            "save_path": "/G3/web/",
        },
        {
            "hash": "direct",
            "name": "Direct Movie",
            "content_path": "/G3/web/Direct Movie.mkv",
            "download_path": "/G3/web/",
            "save_path": "/G3/web/",
        },
        {
            "hash": "other-plugin",
            "name": "Other Movie",
            "content_path": "/临时/Other Movie.mkv",
            "download_path": "/临时/",
            "save_path": "/G3/web/",
        },
    ]

    plan = build_qb_path_repair_plan(torrents, {"broken", "direct"})

    assert plan == [
        {
            "save_path": "/G3/web",
            "hashes": ["broken"],
            "titles": ["Broken Movie"],
        }
    ]


def test_repair_plan_rejects_empty_or_same_temp_destination():
    torrents = [
        {
            "hash": "empty",
            "content_path": "/临时/a.mkv",
            "download_path": "/临时",
            "save_path": "",
        },
        {
            "hash": "same",
            "content_path": "/临时/b.mkv",
            "download_path": "/临时",
            "save_path": "/临时/",
        },
    ]

    assert build_qb_path_repair_plan(torrents, {"empty", "same"}) == []


def test_repair_plan_uses_torrent_download_path_after_global_temp_path_changed():
    torrents = [{
        "hash": "broken",
        "name": "Broken Movie",
        "content_path": "/临时/Broken Movie.mkv",
        "download_path": "/临时",
        "save_path": "/G3/web",
    }]

    plan = build_qb_path_repair_plan(torrents, {"broken"})

    assert plan[0]["hashes"] == ["broken"]
