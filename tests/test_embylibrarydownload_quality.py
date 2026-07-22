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
excluded_tv_reason = quality_module.excluded_tv_reason
select_save_path = quality_module.select_save_path


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


def test_excluded_series_only_rejects_tv_and_checks_aliases():
    assert excluded_tv_reason(
        "Show.Name.S02E03.2160p.WEB-DL",
        "别的剧, Show Name",
        is_tv=True,
    ) == "命中排除剧集：Show Name"
    assert excluded_tv_reason(
        "Localized.Title.S01.1080p",
        "Original Series",
        is_tv=True,
        aliases=["Original Series"],
    ) == "命中排除剧集：Original Series"
    assert excluded_tv_reason("Show Name 2025 2160p", "Show Name", is_tv=False) == ""


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
