from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest


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


def test_webdl_4k_and_1080p_fill_slots_bypass_three_version_cap(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:11"
    inventory = [
        inventory_row("remux", media_key, "remux:dv:2160p"),
        inventory_row("encode", media_key, "encode:hdr10:1080p"),
        inventory_row("diy", media_key, "diy:hdr10:2160p"),
    ]
    inventory[1]["quality_type"] = "encode"
    inventory[2]["quality_type"] = "diy"
    web4k = candidate("web-4k", "webdl:hdr10:2160p")
    web4k.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 25})
    web1080 = candidate("web-1080", "webdl:sdr:1080p")
    web1080.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 12})
    store.replace_inventory("Emby", inventory)
    store.replace_candidates("pool", [web4k, web1080])

    job_4k, reason_4k = store.reserve_download("web-4k", [media_key], 3, None, True)
    job_1080, reason_1080 = store.reserve_download("web-1080", [media_key], 3, None, True)

    assert job_4k and not reason_4k
    assert job_1080 and not reason_1080
    jobs = {item["candidate_key"]: item for item in store.list_jobs()["items"]}
    assert jobs["web-4k"]["webdl_policy"]["mode"] == "fill"
    assert jobs["web-1080"]["webdl_policy"]["mode"] == "fill"


def test_webdl_same_resolution_fill_slot_only_allows_one_active_job(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:12"
    store.replace_inventory("Emby", [inventory_row("remux", media_key)])
    first = candidate("web-dv", "webdl:dv:2160p")
    first.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 30})
    second = candidate("web-hdr", "webdl:hdr10:2160p")
    second.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 35})
    store.replace_candidates("pool", [first, second])

    first_id, _ = store.reserve_download("web-dv", [media_key], 3, None, True)
    second_id, reason = store.reserve_download("web-hdr", [media_key], 3, None, True)

    assert first_id
    assert second_id is None
    assert "2160p 槽位已有任务" in reason


def test_higher_webdl_bitrate_replaces_existing_slot_and_lower_is_rejected(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:13"
    old = inventory_row("old-web", media_key, "webdl:sdr:1080p")
    old.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 10})
    lower = candidate("lower-web", "webdl:hdr10:1080p")
    lower.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 9})
    higher = candidate("higher-web", "webdl:hdr10:1080p")
    higher.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 15})
    store.replace_inventory("Emby", [old])
    store.replace_candidates("pool", [lower, higher])

    lower_id, lower_reason = store.reserve_download("lower-web", [media_key], 1, None, True)
    higher_id, higher_reason = store.reserve_download("higher-web", [media_key], 1, None, True)

    assert lower_id is None
    assert "未提升" in lower_reason
    assert higher_id and not higher_reason
    job = store.list_jobs()["items"][0]
    assert job["webdl_policy"]["mode"] == "upgrade"
    assert job["webdl_policy"]["old_versions"][0]["version_key"] == "old-web"


def test_unknown_bitrate_never_replaces_existing_webdl(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:14"
    old = inventory_row("old-web", media_key, "webdl:hdr10:2160p")
    old.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 20})
    unknown = candidate("unknown-web", "webdl:dv:2160p")
    unknown.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 0})
    store.replace_inventory("Emby", [old])
    store.replace_candidates("pool", [unknown])

    job_id, reason = store.reserve_download("unknown-web", [media_key], 3, None, True)

    assert job_id is None
    assert "码率无法识别" in reason


def test_webdl_upgrade_is_ready_only_after_emby_sees_a_better_new_version(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:15"
    old = inventory_row("old-web", media_key, "webdl:sdr:1080p")
    old.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 10})
    higher = candidate("higher-web", "webdl:hdr10:1080p")
    higher.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 15})
    store.replace_inventory("Emby", [old])
    store.replace_candidates("pool", [higher])
    job_id, _ = store.reserve_download("higher-web", [media_key], 3, None, True)
    store.update_job(job_id, "queued", download_id="download-15")

    not_better = inventory_row("new-low", media_key, "webdl:hdr10:1080p")
    not_better.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 9})
    store.replace_inventory("Emby", [old, not_better])
    assert store.ready_webdl_jobs() == []

    new = inventory_row("new-web", media_key, "webdl:hdr10:1080p")
    new.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 14})
    store.replace_inventory("Emby", [old, new])
    ready = store.ready_webdl_jobs()

    assert [item["id"] for item in ready] == [job_id]
    assert ready[0]["new_versions"][0]["version_key"] == "new-web"
    store.mark_webdl_version_cleaned(job_id, "old-web")
    store.finish_webdl_job(job_id)
    assert store.list_jobs()["items"][0]["status"] == "present"
    assert [item["version_key"] for item in store.list_inventory()["items"]] == ["new-web"]


def test_webdl_upgrade_never_treats_old_path_with_changed_source_id_as_new(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:16"
    old = inventory_row("old-web", media_key, "webdl:sdr:1080p")
    old.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 10})
    higher = candidate("higher-web", "webdl:hdr10:1080p")
    higher.update({"quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 15})
    store.replace_inventory("Emby", [old])
    store.replace_candidates("pool", [higher])
    job_id, _ = store.reserve_download("higher-web", [media_key], 3, None, True)
    store.update_job(job_id, "queued", download_id="download-16")

    changed_id = inventory_row("changed-id", media_key, "webdl:hdr10:1080p")
    changed_id.update({
        "quality_type": "webdl", "resolution": "1080p", "bitrate_mbps": 20,
        "path": old["path"],
    })
    store.replace_inventory("Emby", [changed_id])

    assert store.ready_webdl_jobs() == []


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


@pytest.mark.parametrize(
    ("sort_by", "field", "low", "high"),
    (
        ("year", "year", 2000, 2026),
        ("size", "size_bytes", 1, 2),
        ("seeders", "seeders", 1, 2),
        ("bitrate", "bitrate_mbps", 10, 20),
    ),
)
def test_candidate_sort_options_control_pool_and_download_order(
    tmp_path, sort_by, field, low, high
):
    store = PluginStore(tmp_path / "state.db")
    lower = candidate("lower")
    higher = candidate("higher")
    lower[field] = low
    higher[field] = high
    store.replace_candidates("pool", [lower, higher])

    listed = store.list_candidates(sort_by=sort_by, sort_order="asc")
    pending = store.pending_auto_candidates(sort_by=sort_by, sort_order="desc")

    assert [item["candidate_key"] for item in listed["items"]] == ["lower", "higher"]
    assert [item["candidate_key"] for item in pending] == ["higher", "lower"]


def test_candidate_rating_sort_uses_media_rating_and_keeps_unknown_last(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    unknown = candidate("unknown")
    lower = candidate("lower")
    higher = candidate("higher")
    lower["media_json"] = dumps({"vote_average": 6.5})
    higher["media_json"] = dumps({"vote_average": 8.8})
    store.replace_candidates("pool", [unknown, lower, higher])

    ascending = store.list_candidates(sort_by="rating", sort_order="asc")
    descending = store.pending_auto_candidates(sort_by="rating", sort_order="desc")

    assert [item["candidate_key"] for item in ascending["items"]] == ["lower", "higher", "unknown"]
    assert [item["candidate_key"] for item in descending] == ["higher", "lower", "unknown"]


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


def test_unknown_bitrate_candidates_use_larger_size_as_webdl_fallback(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    smaller = candidate("smaller", "webdl:hdr10:2160p")
    larger = candidate("larger", "webdl:hdr10:2160p")
    for row in (smaller, larger):
        row.update({"quality_type": "webdl", "resolution": "2160p", "bitrate_mbps": 0})
    smaller["quality_score"] = 1_090_000_000
    larger["quality_score"] = 1_010_000_000
    smaller["size_bytes"] = 10 * 1024 ** 3
    larger["size_bytes"] = 20 * 1024 ** 3
    store.replace_candidates("pool", [smaller, larger])

    assert store.pending_auto_candidate_keys() == ["larger", "smaller"]


def test_minimum_size_settings_regrade_existing_scanned_candidates(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    row_4k = candidate("small-4k")
    row_4k["size_bytes"] = 19 * 1024 ** 3
    row_1080p = candidate("large-1080p")
    row_1080p.update({"resolution": "1080p", "size_bytes": 9 * 1024 ** 3})
    store.replace_candidates("pool", [row_4k, row_1080p])

    assert store.apply_minimum_size_filters(20 * 1024 ** 3, 8 * 1024 ** 3) == 1
    assert store.list_candidates()["total"] == 1
    assert store.get_candidate("small-4k")["rejection_reason"].startswith("4K 体积低于最低设置")

    store.apply_minimum_size_filters(0, 0)
    assert store.list_candidates()["total"] == 2
    assert store.get_candidate("small-4k")["rejection_reason"] is None


def test_pending_auto_candidates_exclude_torrents_already_added_to_downloads(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    first = candidate("first")
    duplicate = candidate("duplicate")
    duplicate["torrent_key"] = first["torrent_key"]
    store.replace_candidates("pool", [first, duplicate, candidate("next")])

    job_id, reason = store.reserve_download(
        "first", ["movie:themoviedb:100"], 3, None, True
    )
    assert job_id and not reason
    store.update_job(job_id, "queued", download_id="download-1")

    assert store.pending_auto_candidate_keys() == ["next"]


def test_inventory_rejected_candidate_is_not_retried_until_next_scan(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    store.replace_candidates("pool", [candidate("not-needed"), candidate("needed")])

    store.reject_candidate("not-needed", "媒体库已有更好版本")

    assert store.pending_auto_candidate_keys() == ["needed"]
    assert store.get_candidate("not-needed")["rejection_reason"] == "媒体库已有更好版本"


def test_retry_reservation_replaces_failed_job_with_new_attempt(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    store.replace_candidates("pool", [candidate("retry")])
    media_key = "movie:themoviedb:200"
    job_id, reason = store.reserve_download("retry", [media_key], 3, None, False)
    assert job_id and not reason
    store.update_job(job_id, "failed", error="network error")

    retry_id, retry_reason = store.reserve_download(
        "retry", [media_key], 3, None, False, retry_job_id=job_id
    )

    assert retry_id and retry_id != job_id
    assert not retry_reason
    page = store.list_jobs()
    assert page["total"] == 1
    assert page["items"][0]["id"] == retry_id
    assert page["items"][0]["status"] == "reserved"
    assert page["items"][0]["error"] is None


def test_retryable_jobs_and_bulk_delete_only_use_safe_states(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    store.replace_candidates("pool", [candidate("active"), candidate("failed"), candidate("cancelled")])
    jobs = []
    for index, key in enumerate(("active", "failed", "cancelled"), start=1):
        job_id, reason = store.reserve_download(
            key, [f"movie:themoviedb:{300 + index}"], 3, None, False
        )
        assert job_id and not reason
        jobs.append(job_id)
    store.update_job(jobs[1], "failed", error="network error")
    assert store.cancel_job(jobs[2])[0] is True

    retryable = store.retryable_jobs(jobs)
    failed = store.retryable_jobs(all_failed=True)
    result = store.delete_jobs([*jobs, 999999])

    assert {item["id"] for item in retryable} == {jobs[1], jobs[2]}
    assert [item["id"] for item in failed] == [jobs[1]]
    assert result == {"requested": 4, "deleted": 2, "blocked": 1, "missing": 1}
    assert [item["id"] for item in store.list_jobs()["items"]] == [jobs[0]]


def test_list_jobs_cleans_old_failed_rows_when_same_torrent_is_active(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    failed = candidate("failed-copy")
    active = candidate("active-copy")
    active["torrent_key"] = failed["torrent_key"]
    store.replace_candidates("pool", [failed, active])

    failed_id, reason = store.reserve_download(
        "failed-copy", ["movie:themoviedb:401"], 3, None, False
    )
    assert failed_id and not reason
    store.update_job(failed_id, "failed", error="下载种子内容为空")
    active_id, reason = store.reserve_download(
        "active-copy", ["movie:themoviedb:402"], 3, None, False
    )
    assert active_id and not reason
    store.update_job(active_id, "queued", download_id="download-402")

    page = store.list_jobs()

    assert page["cleaned_count"] == 1
    assert page["failed_count"] == 0
    assert [item["id"] for item in page["items"]] == [active_id]


def test_obsolete_failed_jobs_are_cleaned_for_slot_and_version_cap(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    rows = [
        candidate("slot-failed", "remux:dv:2160p"),
        candidate("slot-active", "remux:dv:2160p"),
        candidate("cap-failed", "webdl:sdr:1080p"),
        candidate("cap-active", "encode:hdr10:2160p"),
    ]
    store.replace_candidates("pool", rows)
    store.replace_inventory("Emby", [
        inventory_row("cap-v1", "movie:themoviedb:502", "bluray:hdr10:2160p"),
        inventory_row("cap-v2", "movie:themoviedb:502", "diy:dv:2160p"),
    ])
    failed_jobs = []
    active_jobs = []
    for key, media_key in (
        ("slot-failed", "movie:themoviedb:501"),
        ("slot-active", "movie:themoviedb:501"),
        ("cap-failed", "movie:themoviedb:502"),
        ("cap-active", "movie:themoviedb:502"),
    ):
        job_id, reason = store.reserve_download(key, [media_key], 3, None, False)
        assert job_id and not reason
        if key.endswith("failed"):
            store.update_job(job_id, "failed", error="下载种子内容为空")
            failed_jobs.append(job_id)
        else:
            store.update_job(job_id, "queued", download_id=f"download-{key}")
            active_jobs.append(job_id)

    cleaned = store.cleanup_obsolete_failed_jobs(max_versions=3, allow_same_slot=False)

    assert cleaned == failed_jobs
    assert [item["id"] for item in store.list_jobs()["items"]] == list(reversed(active_jobs))


def test_target_scanned_pool_preference_is_saved_and_defaults_off(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({"title": "Movie", "auto_download": True})
    assert target["prefer_scanned_pool"] is False

    target = store.save_target(
        {"title": "Movie", "auto_download": True, "prefer_scanned_pool": True},
        target["id"],
    )
    assert target["prefer_scanned_pool"] is True


def test_target_poster_url_is_saved_and_updated(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({
        "title": "电影", "original_title": "Movie",
        "poster_url": "https://img.example/movie.jpg",
    })

    assert target["poster_url"] == "https://img.example/movie.jpg"
    assert target["original_title"] == "Movie"

    target = store.save_target({"title": "Movie", "poster_url": ""}, target["id"])
    assert target["poster_url"] is None


def test_target_list_marks_inventory_presence_and_missing_targets(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    present = store.save_target({
        "title": "Present Movie", "year": 2026,
        "media_source": "themoviedb", "media_id": "10",
    })
    missing = store.save_target({
        "title": "Missing Movie", "year": 2026,
        "media_source": "themoviedb", "media_id": "99",
    })

    unknown = {target["id"]: target for target in store.list_targets(with_inventory=True)}
    assert unknown[present["id"]]["inventory_state"] == "unknown"
    assert unknown[missing["id"]]["inventory_state"] == "unknown"

    store.replace_inventory("Emby", [inventory_row("present-v1", "movie:themoviedb:10")])
    store.mark_inventory_synced()
    targets = {target["id"]: target for target in store.list_targets(with_inventory=True)}

    assert targets[present["id"]]["inventory_state"] == "present"
    assert targets[present["id"]]["in_library"] is True
    assert targets[present["id"]]["inventory_count"] == 1
    assert targets[missing["id"]]["inventory_state"] == "missing"
    assert targets[missing["id"]]["in_library"] is False


def test_recommendation_list_reports_each_poster_inventory_state(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({
        "title": "豆瓣电影 Top 250",
        "recommend_source": "recommend/douban_movie_top250",
        "items": [
            {
                "media_type": "movie", "media_source": "themoviedb", "media_id": "10",
                "title": "Present", "poster_url": "https://img.example/present.jpg", "year": 1993,
            },
            {
                "media_type": "movie", "media_source": "themoviedb", "media_id": "20",
                "title": "Missing", "poster_url": "https://img.example/missing.jpg", "year": 1994,
            },
        ],
    })
    store.replace_inventory("Emby", [inventory_row("present-v1", "movie:themoviedb:10")])
    store.mark_inventory_synced()

    result = {item["id"]: item for item in store.list_targets(with_inventory=True)}[target["id"]]

    assert result["inventory_state"] == "partial"
    assert result["item_count"] == 2
    assert result["in_library_count"] == 1
    assert result["missing_count"] == 1
    assert [item["inventory_state"] for item in result["items"]] == ["present", "missing"]
    assert result["items"][0]["poster_url"].endswith("present.jpg")

    assert store.target_inventory_stats() == {
        "lists": 1,
        "items": 2,
        "present": 1,
        "missing": 1,
    }


def test_recommendation_list_requires_items(tmp_path):
    store = PluginStore(tmp_path / "state.db")

    with pytest.raises(ValueError, match="至少需要一部影片"):
        store.save_target({
            "title": "Empty List", "recommend_source": "recommend/douban_movie_top250",
            "items": [],
        })


def test_target_inventory_title_fallback_uses_original_title_and_year(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({"title": "Original Movie", "year": 2026})
    row = inventory_row("original-v1", "movie:themoviedb:80")
    row.update({"title": "中文电影", "original_title": "Original.Movie", "year": 2026})
    wrong_year = inventory_row("wrong-year", "movie:themoviedb:81")
    wrong_year.update({"title": "Original Movie", "original_title": "Original Movie", "year": 2024})
    store.replace_inventory("Emby", [row, wrong_year])
    store.mark_inventory_synced()

    result = {item["id"]: item for item in store.list_targets(with_inventory=True)}[target["id"]]

    assert result["inventory_state"] == "present"
    assert result["inventory_count"] == 1


def test_douban_target_title_allows_one_year_release_difference(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({
        "title": "豆瓣电影 Top 250",
        "recommend_source": "recommend/douban_movie_top250",
        "items": [{
            "media_type": "movie",
            "media_source": "douban",
            "media_id": "1291546",
            "title": "霸王别姬",
            "year": 1993,
        }],
    })
    present = inventory_row("farewell-v1", "movie:themoviedb:10997")
    present.update({"title": "霸王别姬", "original_title": "霸王别姬", "year": 1992})
    wrong = inventory_row("farewell-wrong", "movie:themoviedb:99999")
    wrong.update({"title": "霸王别姬", "original_title": "霸王别姬", "year": 1991})
    store.replace_inventory("Emby", [present, wrong])
    store.mark_inventory_synced()

    result = {item["id"]: item for item in store.list_targets(with_inventory=True)}[target["id"]]

    assert result["items"][0]["inventory_state"] == "present"
    assert result["items"][0]["inventory_count"] == 1


def test_tv_target_inventory_matches_episode_media_key_prefix(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({
        "title": "Series", "media_type": "tv",
        "media_source": "themoviedb", "media_id": "20",
    })
    first = inventory_row("episode-1", "tv:themoviedb:20:S01E01")
    second = inventory_row("episode-2", "tv:themoviedb:20:S01E02")
    for row in (first, second):
        row["item_type"] = "tv"
    store.replace_inventory("Emby", [first, second])
    store.mark_inventory_synced()

    result = {item["id"]: item for item in store.list_targets(with_inventory=True)}[target["id"]]

    assert result["inventory_state"] == "present"
    assert result["inventory_count"] == 2


def test_pool_reservation_records_matched_target_override(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    target = store.save_target({"title": "Movie", "prefer_scanned_pool": True})
    store.replace_candidates("pool", [candidate("pool-target")])

    job_id, reason = store.reserve_download(
        "pool-target", ["movie:themoviedb:70"], 3, "/movies", True,
        target_id=target["id"],
    )

    assert job_id and not reason
    assert store.list_jobs()["items"][0]["target_id"] == target["id"]


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


def test_inventory_defaults_to_newest_emby_date_created(tmp_path):
    store = PluginStore(tmp_path / "state.db")
    media_key = "movie:themoviedb:60"
    older = inventory_row("older", media_key)
    newer = inventory_row("newer", media_key)
    unknown = inventory_row("unknown", media_key)
    older["date_created"] = "2025-01-01T08:00:00.0000000Z"
    newer["date_created"] = "2026-06-01T08:00:00.0000000Z"
    unknown["date_created"] = None

    store.replace_inventory("Emby", [older, unknown, newer])

    page = store.list_inventory()
    assert [item["version_key"] for item in page["items"]] == ["newer", "older", "unknown"]
    assert page["items"][0]["date_created"] == newer["date_created"]


def test_existing_inventory_database_adds_date_created_column(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE inventory_versions DROP COLUMN date_created")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(inventory_versions)")}

    assert "date_created" in columns


def test_existing_target_database_adds_scanned_pool_preference_column(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE targets DROP COLUMN prefer_scanned_pool")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)")}

    assert "prefer_scanned_pool" in columns


def test_existing_target_database_adds_poster_url_column(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE targets DROP COLUMN poster_url")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)")}

    assert "poster_url" in columns


def test_existing_target_database_adds_original_title_column(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE targets DROP COLUMN original_title")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)")}

    assert "original_title" in columns


def test_existing_target_database_adds_recommendation_list_columns(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE targets DROP COLUMN recommend_source")
        conn.execute("ALTER TABLE targets DROP COLUMN items_json")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)")}

    assert {"recommend_source", "items_json"}.issubset(columns)


def test_existing_download_database_adds_webdl_policy_column(tmp_path):
    path = tmp_path / "state.db"
    store = PluginStore(path)
    with store.connect() as conn:
        conn.execute("ALTER TABLE download_jobs DROP COLUMN webdl_policy_json")

    migrated = PluginStore(path)
    with migrated.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(download_jobs)")}

    assert "webdl_policy_json" in columns
