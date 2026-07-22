from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "sources.py"
)
SPEC = spec_from_file_location("embylibrarydownload_sources", MODULE_PATH)
sources_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = sources_module
SPEC.loader.exec_module(sources_module)

UBITS_MOVIE_SOURCES = sources_module.UBITS_MOVIE_SOURCES
build_category_site = sources_module.build_category_site
is_ubits_domain = sources_module.is_ubits_domain
should_continue_pages = sources_module.should_continue_pages


def test_ubits_movie_sources_use_the_four_requested_filters_and_page_placeholder():
    sources = {source.quality_type: source for source in UBITS_MOVIE_SOURCES}

    assert set(sources) == {"webdl", "remux", "diy", "encode"}
    expected = {
        "webdl": {"team6": ["1"]},
        "remux": {"medium3": ["1"], "team1": ["1"]},
        "diy": {"medium10": ["1"], "medium1": ["1"], "team1": ["1"]},
        "encode": {"medium7": ["1"], "team1": ["1"]},
    }
    for quality_type, source in sources.items():
        parsed = urlsplit(source.path)
        query = parse_qs(parsed.query)
        assert parsed.path == "torrents.php"
        assert "page={page}" in source.path
        for key, value in expected[quality_type].items():
            assert query[key] == value


def test_category_site_preserves_parser_fields_and_replaces_only_browse_path():
    site = {
        "id": 7,
        "domain": "https://ubits.club/",
        "browse": {"path": "old?page={page}", "start": 1, "list": {"selector": "tr"}},
        "torrents": {"fields": {"title": {"selector": "a"}}},
    }

    result = build_category_site(site, UBITS_MOVIE_SOURCES[0])

    assert result is not site
    assert result["browse"]["path"] == UBITS_MOVIE_SOURCES[0].path
    assert result["browse"]["start"] == 0
    assert result["browse"]["list"] == {"selector": "tr"}
    assert result["torrents"] == site["torrents"]


def test_ubits_domain_and_all_page_stop_rules():
    assert is_ubits_domain("https://ubits.club/") is True
    assert is_ubits_domain("https://www.ubits.club") is True
    assert is_ubits_domain("https://example.com/ubits.club") is False
    assert should_continue_pages(result_count=100, page_size=100, new_count=100) is True
    assert should_continue_pages(result_count=99, page_size=100, new_count=99) is False
    assert should_continue_pages(result_count=100, page_size=100, new_count=0) is False
