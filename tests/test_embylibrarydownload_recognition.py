from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "recognition.py"
)
SPEC = spec_from_file_location("embylibrarydownload_recognition", MODULE_PATH)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_target_identity_is_the_first_recognition_attempt():
    attempts = MODULE.build_recognition_attempts(
        {
            "title": "Hanataba mitaina koi o shita 2021 1080p WEB-DL",
            "description": "花束般的恋爱/她和他的恋爱花期(港) | 导演: 土井裕泰",
            "year": 2021,
        },
        {
            "title": "花束般的恋爱",
            "year": 2021,
            "media_type": "movie",
            "media_source": "douban",
            "media_id": "34874432",
        },
    )

    assert attempts[0] == {
        "title": "花束般的恋爱 2021",
        "source": "douban",
        "media_id": "34874432",
        "media_type": "movie",
    }
    assert attempts[1] == {
        "title": "花束般的恋爱 2021",
        "source": "",
        "media_id": "",
        "media_type": "movie",
    }
    assert attempts[2]["title"].startswith("Hanataba mitaina koi o shita 2021")


def test_chinese_description_title_is_used_after_raw_title():
    attempts = MODULE.build_recognition_attempts(
        {
            "title": "Mission Against Drugs 2026 2160p WEB-DL HQ",
            "description": "缉毒使命 | 导演: 赵锐勇 张勇 | 主演: 泽南",
            "year": 2026,
        },
        None,
    )

    assert attempts[0]["title"] == "Mission Against Drugs 2026 2160p WEB-DL HQ"
    assert attempts[1]["title"] == "缉毒使命 2026"
    assert all(not item["source"] and not item["media_id"] for item in attempts)
