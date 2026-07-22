from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "quality.py"
)
SPEC = spec_from_file_location("embylibrarydownload_quality", MODULE_PATH)
quality_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = quality_module
SPEC.loader.exec_module(quality_module)

classify_quality = quality_module.classify_quality
quality_matches = quality_module.quality_matches
profile_score = quality_module.profile_score
apply_source_quality_type = quality_module.apply_source_quality_type
is_series_title = quality_module.is_series_title
tv_exclusion_reason = quality_module.tv_exclusion_reason
select_save_path = quality_module.select_save_path
prioritize_pool_candidates = quality_module.prioritize_pool_candidates


def test_classifies_diy_dolby_vision_and_bitrate():
    result = classify_quality("Film.2025.2160p.UHD.BluRay.DIY.DV.HEVC.68Mbps")

    assert result.quality_type == "diy"
    assert result.effect == "dv"
    assert result.resolution == "2160p"
    assert result.video_codec == "h265"
    assert result.bitrate_mbps == 68
    assert result.year == 2025


def test_remux_is_not_misclassified_as_bluray():
    result = classify_quality("Film.2024.1080p.BluRay.REMUX.AVC.DTS-HD.MA")

    assert result.quality_type == "remux"
    assert result.resolution == "1080p"
    assert result.video_codec == "h264"


def test_bluray_encode_and_full_disc_are_distinct():
    encode = classify_quality("Film.2024.1080p.BluRay.x264.DTS")
    full_disc = classify_quality("Film.2024.2160p.UHD.BluRay.BDMV.HEVC")

    assert encode.quality_type == "encode"
    assert full_disc.quality_type == "bluray"


def test_profile_filters_keywords_and_quality():
    profile = {
        "quality_types": ["remux", "diy"],
        "effects": ["dv", "hdr10"],
        "resolutions": ["2160p"],
        "min_bitrate_mbps": 30,
        "exclude_words": "CAM,TS",
    }

    accepted = classify_quality("Film.2024.2160p.Remux.DV.55Mbps")
    rejected = classify_quality("Film.2024.2160p.WEB-DL.HDR10.18Mbps")

    assert quality_matches("Film.2024.2160p.Remux.DV.55Mbps", accepted, profile) == (True, "")
    assert quality_matches("Film.2024.2160p.WEB-DL.HDR10.18Mbps", rejected, profile)[0] is False


def test_short_exclude_word_does_not_reject_dts_or_atmos():
    profile = {"exclude_words": "CAM,TS,TC,HDTS"}
    quality = classify_quality("Film.2024.2160p.Remux.DV.DTS-HD.MA.Atmos")

    assert quality_matches("Film.2024.2160p.Remux.DV.DTS-HD.MA.Atmos", quality, profile)[0] is True
    assert quality_matches("Film.2024.1080p.TS.H264", classify_quality("Film.2024.1080p.TS.H264"), profile)[0] is False


def test_profile_order_and_bitrate_direction_control_ranking():
    remux_low = classify_quality("Film.2024.2160p.Remux.DV.30Mbps")
    encode_high = classify_quality("Film.2024.2160p.x265.DV.70Mbps")
    profile = {"quality_types": ["encode", "remux"], "bitrate_order": "desc"}

    assert profile_score(encode_high, profile) > profile_score(remux_low, profile)

    profile = {"quality_types": ["remux"], "bitrate_order": "asc"}
    remux_high = classify_quality("Film.2024.2160p.Remux.DV.70Mbps")
    assert profile_score(remux_low, profile) > profile_score(remux_high, profile)


def test_tv_exclusion_switch_rejects_every_series_only_when_enabled():
    assert tv_exclusion_reason(is_tv=True, enabled=True) == "已启用排除剧集"
    assert tv_exclusion_reason(is_tv=False, enabled=True) == ""
    assert tv_exclusion_reason(is_tv=True, enabled=False) == ""


def test_category_source_is_authoritative_for_quality_type_and_slot():
    detected = classify_quality("Film.2025.2160p.HEVC")
    result = apply_source_quality_type(detected, "remux")

    assert result.quality_type == "remux"
    assert result.slot.startswith("remux:")
    assert result.score > detected.score


def test_series_titles_are_detected_without_rejecting_movie_titles():
    assert is_series_title("Show.Name.S01E02.2160p.WEB-DL") is True
    assert is_series_title("Show Name Season 2 Complete 1080p") is True
    assert is_series_title("节目名称 第三季 全集") is True
    assert is_series_title("Se7en.1995.2160p.Remux") is False


def test_save_path_precedence_is_target_then_quality_then_media_type():
    config = {
        "movie_save_path": "storage:/movies",
        "tv_save_path": "storage:/tv",
        "quality_save_paths": {"remux": "storage:/remux"},
    }

    assert select_save_path(config, "remux", "movie", "storage:/target") == "storage:/target"
    assert select_save_path(config, "remux", "tv") == "storage:/remux"
    assert select_save_path(config, "webdl", "tv") == "storage:/tv"
    assert select_save_path(config, "webdl", "movie") == "storage:/movies"


def test_matching_scanned_target_is_moved_before_newer_regular_candidate():
    base_profile = {"quality_types": ["remux", "webdl"], "resolutions": ["2160p"]}
    target = {
        "id": 1, "enabled": True, "auto_download": True, "prefer_scanned_pool": True,
        "media_type": "movie", "title": "Dune Part Two", "year": 2024,
        "sites": [7], "profile": {"quality_types": ["remux"]},
    }
    regular = {
        "candidate_key": "newer", "eligible": True, "title": "Other.Movie.2026.2160p.WEB-DL",
        "year": 2026, "site_id": 7, "quality_type": "webdl", "quality_effect": "hdr",
        "resolution": "2160p", "video_codec": "h265", "bitrate_mbps": 20,
    }
    matched = {
        "candidate_key": "target", "eligible": True, "title": "Dune.Part.Two.2024.2160p.Remux",
        "year": 2024, "site_id": 7, "quality_type": "remux", "quality_effect": "dv",
        "resolution": "2160p", "video_codec": "h265", "bitrate_mbps": 60,
    }

    ordered = prioritize_pool_candidates([regular, matched], [target], base_profile, [7])

    assert [candidate["candidate_key"] for candidate, _ in ordered] == ["target", "newer"]
    assert ordered[0][1]["id"] == 1
    assert ordered[1][1] is None

    target["prefer_scanned_pool"] = False
    unchanged = prioritize_pool_candidates([regular, matched], [target], base_profile, [7])
    assert [candidate["candidate_key"] for candidate, _ in unchanged] == ["newer", "target"]


def test_scanned_target_priority_requires_target_site_year_and_filter_match():
    target = {
        "id": 1, "enabled": True, "auto_download": True, "prefer_scanned_pool": True,
        "media_type": "movie", "title": "Dune Part Two", "year": 2024,
        "sites": [7], "profile": {"quality_types": ["remux"]},
    }
    candidate = {
        "candidate_key": "wrong", "eligible": True, "title": "Dune.Part.Two.2025.2160p.WEB-DL",
        "year": 2025, "site_id": 8, "quality_type": "webdl", "quality_effect": "hdr",
        "resolution": "2160p", "video_codec": "h265", "bitrate_mbps": 20,
    }

    assert prioritize_pool_candidates([candidate], [target], {}, [7])[0][1] is None
