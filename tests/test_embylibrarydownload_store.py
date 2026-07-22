from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor


MODULE_PATH = Path(__file__).parents[1] / "plugins.v2" / "embylibrarydownload" / "store.py"
SPEC = spec_from_file_location("embylibrarydownload_store", MODULE_PATH)
store_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = store_module
SPEC.loader.exec_module(store_module)
PluginStore = store_module.PluginStore
dumps = store_module.dumps


def candidate(key, slot="remux:dv:2160p"):
    return {
        "candidate_key": key,
        "torrent_key": key,
        "site_id": 1,
        "site_name": "Test",
        "title": key,
        "description": "",
        "page_url": "https://example.test/details/1",
        "enclosure": "https://example.test/download/1",
        "size_bytes": 1,
        "seeders": 10,
        "peers": 0,
        "pubdate": "2026-01-01 00:00:00",
        "year": 2026,
        "quality_type": "remux",
        "quality_effect": "dv",
        "resolution": "2160p",
        "video_codec": "h265",
        "bitrate_mbps": 50,
        "quality_score": 700,
        "quality_slot": slot,
        "eligible": 1,
        "torrent_json": dumps({"title": key}),
    }


def inventory_row(version_key, media_key, slot="remux:dv:2160p"):
    return {
        "version_key": version_key,
        "media_key": media_key,
        "server_name": "Emby",
        "library_id": "movies",
        "item_id": version_key,
        "media_source_id": version_key,
        "item_type": "movie",
        "title": "Movie",
        "original_title": "Movie",
        "year": 2026,
        "season": None,
        "episode": None,
        "path": f"/{version_key}.mkv",
        "tmdb_id": "10",
        "imdb_id": None,
        "tvdb_id": None,
        "quality_type": "remux",
        "quality_effect": "dv",
        "resolution": "2160p",
        "video_codec": "h265",
        "audio_codec": "truehd",
        "bitrate_mbps": 50,
        "size_bytes": 1,
        "quality_slot": slot,
    }


def test_cap_includes_existing_and_reserved_jobs(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:10"
    store.replace_inventory("Emby", [inventory_row("v1", media_key, "webdl:sdr:1080p")])
    store.replace_candidates("pool", [candidate("c1"), candidate("c2", "encode:hdr10:2160p")])

    job_id, reason = store.reserve_download("c1", [media_key], 2, None, False)
    blocked_id, blocked_reason = store.reserve_download("c2", [media_key], 2, None, False)

    assert job_id and not reason
    assert blocked_id is None
    assert "2 个版本上限" in blocked_reason


def test_same_quality_slot_is_rejected(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:10"
    store.replace_inventory("Emby", [inventory_row("v1", media_key)])
    store.replace_candidates("pool", [candidate("c1")])

    job_id, reason = store.reserve_download("c1", [media_key], 3, None, False)

    assert job_id is None
    assert "相同质量槽位" in reason


def test_candidate_pages_are_fixed_to_fifty_and_year_descending(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    rows = []
    for index in range(55):
        row = candidate(f"c{index}")
        row["year"] = 1970 + index
        rows.append(row)
    store.replace_candidates("pool", rows)

    first = store.list_candidates(page=1, page_size=200)
    second = store.list_candidates(page=2, page_size=50)

    assert len(first["items"]) == 50
    assert first["page_size"] == 50
    assert first["items"][0]["year"] == 2024
    assert len(second["items"]) == 5


def test_pool_candidates_are_filtered_and_counted_by_quality_category(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    rows = []
    for key, quality_type in (
        ("webdl", "webdl"),
        ("remux-a", "remux"),
        ("remux-b", "remux"),
        ("diy", "diy"),
        ("encode", "encode"),
    ):
        row = candidate(key)
        row["quality_type"] = quality_type
        rows.append(row)
    store.replace_candidates("pool", rows)

    result = store.list_candidates(scope="pool", quality_type="remux")

    assert result["total"] == 2
    assert {item["quality_type"] for item in result["items"]} == {"remux"}
    assert result["quality_counts"] == {
        "webdl": 1,
        "remux": 2,
        "diy": 1,
        "encode": 1,
    }


def test_tv_season_cap_counts_episode_versions(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    episode_key = "tv:themoviedb:20:S01E01"
    row = inventory_row("episode-v1", episode_key, "webdl:sdr:1080p")
    row.update({"item_type": "tv", "season": 1, "episode": 1})
    store.replace_inventory("Emby", [row])
    store.replace_candidates("pool", [candidate("season-pack", "remux:dv:2160p")])

    job_id, reason = store.reserve_download(
        "season-pack", ["tv:themoviedb:20:S01"], 1, None, False
    )

    assert job_id is None
    assert "1 个版本上限" in reason


def test_tv_episode_key_counts_exact_versions(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    episode_key = "tv:themoviedb:20:S01E02"
    first = inventory_row("episode-v1", episode_key, "webdl:sdr:1080p")
    second = inventory_row("episode-v2", episode_key, "remux:dv:2160p")
    for row in (first, second):
        row.update({"item_type": "tv", "season": 1, "episode": 2})
    store.replace_inventory("Emby", [first, second])

    assert store.inventory_version_count(episode_key) == 2


def test_atomic_reservation_allows_only_one_job_at_cap_one(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:30"
    store.replace_candidates("pool", [
        candidate("parallel-a", "remux:dv:2160p"),
        candidate("parallel-b", "encode:hdr10:1080p"),
    ])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda key: store.reserve_download(key, [media_key], 1, None, False),
            ["parallel-a", "parallel-b"],
        ))

    assert sum(1 for job_id, _ in results if job_id) == 1


def test_same_torrent_can_be_listed_in_two_scopes_but_only_queued_once(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    pool = candidate("pool-key", "remux:dv:2160p")
    target = candidate("target-key", "remux:dv:2160p")
    pool["torrent_key"] = target["torrent_key"] = "same-torrent"
    store.replace_candidates("pool", [pool])
    store.replace_candidates("target:1", [target])

    assert store.list_candidates(scope="pool")["total"] == 1
    assert store.list_candidates(scope="target:1")["total"] == 1

    first_id, _ = store.reserve_download("pool-key", ["movie:themoviedb:40"], 3, None, False)
    second_id, reason = store.reserve_download("target-key", ["movie:themoviedb:40"], 3, None, False)

    assert first_id
    assert second_id is None
    assert "已在下载队列" in reason


def test_inventory_sync_replaces_deselected_libraries_and_servers(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    movie = "movie:themoviedb:50"
    first = inventory_row("lib-a", movie)
    second = inventory_row("lib-b", movie, "encode:hdr10:1080p")
    first["server_name"] = second["server_name"] = "Emby-A"
    second["library_id"] = "other"
    store.replace_inventory("Emby-A", [first, second])
    other_server = inventory_row("server-b", movie, "webdl:sdr:1080p")
    other_server["server_name"] = "Emby-B"
    store.replace_inventory("Emby-B", [other_server])

    store.replace_inventory("Emby-A", [first])
    store.prune_inventory_servers(["Emby-A"])

    page = store.list_inventory()
    assert page["total"] == 1
    assert page["items"][0]["library_id"] == "movies"
    assert page["items"][0]["server_name"] == "Emby-A"


def test_inventory_readiness_is_recorded_even_for_empty_library(tmp_path):
    store = PluginStore(tmp_path / "state.db")

    assert store.inventory_ready() is False
    store.replace_inventory("Emby", [])
    store.mark_inventory_synced()

    assert store.inventory_ready() is True
    assert store.stats()["latest_inventory"]
