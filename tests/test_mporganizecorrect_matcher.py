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
