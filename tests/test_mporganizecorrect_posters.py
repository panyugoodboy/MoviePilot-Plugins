from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "mporganizecorrect"
    / "posters.py"
)
SPEC = spec_from_file_location("mporganizecorrect_posters", MODULE_PATH)
posters = module_from_spec(SPEC)
sys.modules[SPEC.name] = posters
SPEC.loader.exec_module(posters)


def test_tmdb_relative_poster_uses_moviepilot_image_builder():
    calls = []

    def image_url(path, size):
        calls.append((path, size))
        return f"https://tmdb.example/t/p/{size}/{path.lstrip('/')}"

    result = posters.normalize_poster_url("/poster.jpg", "themoviedb", image_url)

    assert result == "https://tmdb.example/t/p/w500/poster.jpg"
    assert calls == [("/poster.jpg", "w500")]


def test_poster_proxy_allows_metadata_hosts_and_rejects_ssrf_targets():
    assert posters.safe_poster_url("https://image.tmdb.org/t/p/w500/poster.jpg")
    assert posters.safe_poster_url(
        "https://img3.doubanio.com/view/photo/m_ratio_poster/public/test.webp"
    )
    assert posters.safe_poster_url("https://tmdb.example/poster.jpg", ["tmdb.example"])
    assert posters.safe_poster_url("file:///etc/passwd") == ""
    assert posters.safe_poster_url("http://127.0.0.1/private.jpg") == ""
    assert posters.safe_poster_url("https://example.com/private.jpg") == ""


def test_douban_poster_request_has_required_referer():
    url = "https://img3.doubanio.com/view/photo/m_ratio_poster/public/test.webp"

    assert posters.poster_request_headers(url)["Referer"] == "https://movie.douban.com/"
