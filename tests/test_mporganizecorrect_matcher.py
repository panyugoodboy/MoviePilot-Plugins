from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "plugins.v2" / "mporganizecorrect" / "matcher.py"
SPEC = spec_from_file_location("mporganizecorrect_matcher", MODULE_PATH)
matcher = module_from_spec(SPEC)
sys.modules[SPEC.name] = matcher
SPEC.loader.exec_module(matcher)


def test_english_destination_with_chinese_source_is_detected():
    assert matcher.is_english_label("The Wandering Earth") is True
    assert matcher.is_english_label("流浪地球") is False


def test_source_identity_prefers_parsed_chinese_title_and_year():
    title, year = matcher.extract_source_identity(
        r"D:\Downloads\The.Wandering.Earth.2019.2160p.mkv",
        parsed_title="流浪地球",
        parsed_year="2019",
        history_year="2019",
    )

    assert title == "流浪地球"
    assert year == 2019


def test_source_identity_falls_back_to_chinese_path_and_strips_technical_tokens():
    title, year = matcher.extract_source_identity(
        r"D:\Downloads\流浪地球.2019.2160p.WEB-DL.x265.mkv"
    )

    assert title == "流浪地球"
    assert year == 2019


def test_source_identity_keeps_only_chinese_title_from_mixed_release_names():
    cases = {
        "恶徒 Hell -HD MA 5": "恶徒",
        "无望的人们 The Round Up USA -HD MA 2": "无望的人们",
        "索命哨 Whistle -HD MA 5": "索命哨",
        "碧海蓝天 The Big Blue Directors Cut FRA UHD -HD MA 5": "碧海蓝天",
        "痴迷 Obsession USA UHD Atmos TrueHD 7": "痴迷",
        "疾速追杀：芭蕾杀姬 Ballerina USA UHD Atmos TrueHD 7 1": "疾速追杀：芭蕾杀姬",
        "独自一人 Alone FRA 1080i 2": "独自一人",
        "K歌情人 Music and Lyrics": "K歌情人",
        "007：无暇赴死 No Time to Die": "007：无暇赴死",
        "阿凡达2 The Way of Water": "阿凡达2",
    }

    for parsed_title, expected_title in cases.items():
        title, year = matcher.extract_source_identity(
            rf"D:\Downloads\{parsed_title}.2025.mkv",
            parsed_title=parsed_title,
            parsed_year="2025",
        )
        assert title == expected_title
        assert year == 2025


def test_source_identity_falls_back_to_clean_english_title_and_year():
    title, year = matcher.extract_source_identity(
        r"D:\Downloads\The.Wandering.Earth.2019.2160p.WEB-DL.x265-GROUP.mkv",
        parsed_title="",
        parsed_year="",
    )

    assert title == "The Wandering Earth"
    assert year == 2019


def test_source_identity_still_prefers_chinese_path_over_parsed_english_title():
    title, year = matcher.extract_source_identity(
        r"D:\Downloads\流浪地球.2019\The.Wandering.Earth.2019.mkv",
        parsed_title="The Wandering Earth",
        parsed_year="2019",
    )

    assert title == "流浪地球"
    assert year == 2019


def test_exact_candidate_requires_unique_title_year_and_type():
    candidates = [
        {
            "title": "流浪地球",
            "names": [],
            "year": 2019,
            "media_type": "电影",
            "media_source": "themoviedb",
            "media_id": "535167",
        },
        {
            "title": "流浪地球",
            "names": [],
            "year": 2023,
            "media_type": "电影",
            "media_source": "themoviedb",
            "media_id": "842675",
        },
    ]

    selected, reason = matcher.choose_exact_candidate("流浪地球", 2019, "电影", candidates)

    assert selected["media_id"] == "535167"
    assert "唯一精确匹配" in reason


def test_exact_candidate_accepts_english_original_title():
    candidate = {
        "title": "流浪地球",
        "original_title": "The Wandering Earth",
        "names": [],
        "year": 2019,
        "media_type": "电影",
        "media_source": "themoviedb",
        "media_id": "535167",
    }

    selected, reason = matcher.choose_exact_candidate(
        "The Wandering Earth", 2019, "电影", [candidate]
    )

    assert selected["media_id"] == "535167"
    assert "唯一精确匹配" in reason


def test_ambiguous_exact_candidates_never_enter_automatic_match():
    candidate = {
        "title": "英雄",
        "names": [],
        "year": 2002,
        "media_type": "电影",
        "media_source": "themoviedb",
    }

    selected, reason = matcher.choose_exact_candidate(
        "英雄",
        2002,
        "电影",
        [{**candidate, "media_id": "1"}, {**candidate, "media_id": "2"}],
    )

    assert selected is None
    assert "人工选择" in reason


def test_move_mode_is_forced_to_copy_and_unknown_mode_is_blocked():
    assert matcher.safe_transfer_mode("move") == "copy"
    assert matcher.safe_transfer_mode("link") == "link"
    assert matcher.safe_transfer_mode("unknown") is None


def test_cleanup_rejects_source_and_new_target_paths():
    safe, reason = matcher.cleanup_paths_are_safe(
        source_storage="local",
        source_path="D:/Downloads/movie.mkv",
        old_storage="local",
        old_path="D:/Downloads/movie.mkv",
        new_storage="local",
        new_path="D:/Library/电影/movie.mkv",
    )
    assert safe is False
    assert "源文件" in reason

    safe, reason = matcher.cleanup_paths_are_safe(
        source_storage="local",
        source_path="D:/Downloads/movie.mkv",
        old_storage="local",
        old_path="D:/Library/电影/movie.mkv",
        new_storage="local",
        new_path="D:/Library/电影/movie.mkv",
    )
    assert safe is False
    assert "新整理目标" in reason


def test_cleanup_accepts_distinct_old_destination():
    safe, reason = matcher.cleanup_paths_are_safe(
        source_storage="local",
        source_path="D:/Downloads/流浪地球.2019.mkv",
        old_storage="local",
        old_path="D:/Library/The Wandering Earth (2019)/movie.mkv",
        new_storage="local",
        new_path="D:/Library/流浪地球 (2019)/movie.mkv",
    )

    assert safe is True
    assert reason == ""


def test_cleanup_accepts_manual_delete_without_new_target():
    safe, reason = matcher.cleanup_paths_are_safe(
        source_storage="local",
        source_path="D:/Downloads/流浪地球.2019.mkv",
        old_storage="local",
        old_path="D:/Library/The Wandering Earth (2019)/movie.mkv",
        new_storage="",
        new_path="",
    )

    assert safe is True
    assert reason == ""
