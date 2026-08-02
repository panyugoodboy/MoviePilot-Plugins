from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "metadata.py"
)
SPEC = spec_from_file_location("embylibrarydownload_metadata", MODULE_PATH)
metadata = module_from_spec(SPEC)
sys.modules[SPEC.name] = metadata
SPEC.loader.exec_module(metadata)


def test_select_movie_metadata_requires_matching_title_and_two_year_range():
    item = {"title": "Citizen Kane", "year": 1941, "year_tolerance": 2}
    candidates = [
        {"type": "电影", "title": "公民凯恩", "original_title": "Citizen Kane", "year": 1941},
        {"type": "电影", "title": "Citizen Kane", "original_title": "Citizen Kane", "year": 2006},
        {"type": "电视剧", "title": "公民凯恩", "original_title": "Citizen Kane", "year": 1941},
    ]

    selected = metadata.select_movie_metadata(item, candidates)

    assert selected["title"] == "公民凯恩"
    assert selected["year"] == 1941


def test_select_movie_metadata_rejects_same_year_partial_title():
    item = {"title": "The Shining", "year": 1980, "year_tolerance": 2}

    selected = metadata.select_movie_metadata(
        item,
        [{
            "type": "电影",
            "title": "Making 'The Shining'",
            "original_title": "Making 'The Shining'",
            "year": 1980,
        }],
    )

    assert selected is None


def test_select_movie_metadata_accepts_top_localized_alias_with_poster():
    item = {"title": "Parasite", "year": 2019, "year_tolerance": 2}

    selected = metadata.select_movie_metadata(
        item,
        [{
            "type": "movie",
            "title": "寄生虫",
            "original_title": "기생충",
            "year": 2019,
            "poster_path": "https://image.tmdb.org/poster.jpg",
        }],
    )

    assert selected["title"] == "寄生虫"


def test_scraped_item_uses_chinese_alias_and_preserves_original_title():
    item = {"title": "Citizen Kane", "year": 1941, "position": 0}
    media = {
        "type": "电影",
        "source": "themoviedb",
        "media_id": "15",
        "title": "Citizen Kane",
        "names": ["市民ケーン", "公民凯恩"],
        "original_title": "Citizen Kane",
        "year": "1941",
        "poster_path": "https://image.tmdb.org/t/p/original/poster.jpg",
    }

    result = metadata.scraped_item(item, media, "2026-08-03T00:00:00+08:00")

    assert result["title"] == "公民凯恩"
    assert result["original_title"] == "Citizen Kane"
    assert result["media_source"] == "themoviedb"
    assert result["media_id"] == "15"
    assert result["metadata_state"] == "complete"
    assert metadata.has_complete_metadata(result) is True


def test_scraped_item_uses_douban_id_for_fallback_metadata():
    result = metadata.scraped_item(
        {"title": "Parasite", "year": 2019},
        {
            "type": "movie",
            "source": "douban",
            "douban_id": "1295644",
            "title": "寄生虫",
            "original_title": "기생충",
            "year": 2019,
            "poster_url": "https://img.example/poster.jpg",
        },
        "2026-08-03T00:00:00+08:00",
    )

    assert result["media_source"] == "douban"
    assert result["media_id"] == "1295644"
    assert result["title"] == "寄生虫"
