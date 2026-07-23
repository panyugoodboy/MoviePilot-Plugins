from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "posters.py"
)
SPEC = spec_from_file_location("embylibrarydownload_posters", MODULE_PATH)
posters = module_from_spec(SPEC)
sys.modules[SPEC.name] = posters
SPEC.loader.exec_module(posters)


def test_douban_poster_uses_referer_required_by_image_host():
    url = posters.safe_poster_url(
        "https://img3.doubanio.com/view/photo/m_ratio_poster/public/p480747492.webp"
    )

    assert url.endswith("p480747492.webp")
    assert posters.poster_request_headers(url)["Referer"] == "https://movie.douban.com/"


def test_poster_proxy_rejects_non_image_hosts_and_unsafe_schemes():
    assert posters.safe_poster_url("file:///etc/passwd") == ""
    assert posters.safe_poster_url("http://127.0.0.1/private") == ""
    assert posters.safe_poster_url("https://example.com/image.jpg") == ""
