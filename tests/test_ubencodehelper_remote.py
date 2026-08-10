import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_CLIENT_PATH = ROOT / "plugins.v2" / "ubencodehelper" / "api_client.py"
NOTIFICATION_SERVICE_PATH = ROOT / "plugins.v2" / "ubencodehelper" / "notification_service.py"


def load_api_client():
    spec = importlib.util.spec_from_file_location("ubencodehelper_api_client_test", API_CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_notification_service():
    app_module = types.ModuleType("app")
    schemas_module = types.ModuleType("app.schemas")
    schemas_module.NotificationType = types.SimpleNamespace(Plugin="plugin")
    original_app = sys.modules.get("app")
    original_schemas = sys.modules.get("app.schemas")
    sys.modules["app"] = app_module
    sys.modules["app.schemas"] = schemas_module
    try:
        spec = importlib.util.spec_from_file_location("ubencodehelper_notification_test", NOTIFICATION_SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if original_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = original_app
        if original_schemas is None:
            sys.modules.pop("app.schemas", None)
        else:
            sys.modules["app.schemas"] = original_schemas


def test_remote_command_api_targets_explicit_device_and_uses_idempotency():
    api = load_api_client()
    calls = []

    class Response:
        ok = True

        @staticmethod
        def json():
            return {"ok": True, "command": {"command_id": "cmd_1"}}

    original = api.requests.request
    try:
        api.requests.request = lambda **kwargs: calls.append(kwargs) or Response()
        client = api.UBencodeApiClient("moviepilot-device")
        result = client.create_remote_command(
            "token", "ubencode-device-a", "generate_screenshots", "item_1",
            {"conflict_policy": "keep"}, "interaction-1",
        )
        client.remote_command("token", result["command"]["command_id"])
    finally:
        api.requests.request = original

    assert calls[0]["url"].endswith("/api/ubencode/remote-commands")
    assert calls[0]["json"]["target_device_id"] == "ubencode-device-a"
    assert calls[0]["json"]["idempotency_key"] == "interaction-1"
    assert calls[1]["url"].endswith("/api/ubencode/remote-commands/cmd_1")


def test_latest_release_api_and_version_comparison():
    api = load_api_client()
    calls = []

    class Response:
        ok = True

        @staticmethod
        def json():
            return {"release": {"version": "1.7.0", "changelog": ["修复更新检查"]}}

    original = api.requests.request
    try:
        api.requests.request = lambda **kwargs: calls.append(kwargs) or Response()
        client = api.UBencodeApiClient("moviepilot-device")
        result = client.latest_release("token")
    finally:
        api.requests.request = original

    assert result["release"]["version"] == "1.7.0"
    assert calls[0]["url"].endswith("/api/ubencode/releases/latest")
    assert calls[0]["params"]["client_version"] == api.CLIENT_VERSION
    assert api.is_newer_version("1.7.0", "1.6.2")
    assert not api.is_newer_version("1.6.2", "1.6.2")


def test_client_update_notification_includes_changelog():
    service = load_notification_service().NotificationService
    text = service.client_update_text(
        {
            "version": "1.7.0",
            "changelog": ["新增客户端版本检查", {"version": "1.6.3", "content": "修复问题"}],
        },
        "1.6.2",
    )
    assert "发现 UBencode 客户端新版本：1.7.0" in text
    assert "当前版本：1.6.2" in text
    assert "• 新增客户端版本检查" in text
    assert "• 1.6.3：修复问题" in text
    assert "• 服务端未提供更新说明" in service.client_update_text({"version": "1.7.0"}, "1.6.2")


def test_plugin_registers_multi_client_buttons_and_two_stage_publish():
    source = (ROOT / "plugins.v2" / "ubencodehelper" / "__init__.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))

    assert "EventType.MessageAction" in source
    assert "target_device_id" in source
    assert "generate_screenshots" in source
    assert "prepare_publish" in source
    assert "confirm_publish" in source
    assert "confirm_token" in source
    assert manifest["UBencodeHelper"]["version"] == "1.3.2"


def test_notifications_append_source_percentage_to_absolute_mbps():
    service = load_notification_service().NotificationService
    round_event = {
        "event_type": "precise_test_round_completed",
        "payload": {
            "title": "Example 2026",
            "round": 2,
            "crf": 21.4,
            "bitrate_mbps": 24.56,
            "bitrate_percent": 37.8,
            "source_bitrate_mbps": 65.0,
            "target_min_mbps": 20,
            "target_max_mbps": 30,
            "denoise_preset": "light",
            "hit_target": True,
            "sample_duration": 60,
        },
    }
    text = service.event_text(round_event)
    assert "视频码率：24.56 Mbps（源码率的 37.8%）" in text
    assert "目标区间：20.0 - 30.0 Mbps（源码率的 30.8% - 46.2%）" in text
    assert "码率比例" not in text
    assert "24.56 Mbps（源码率的 37.8%）" in service.timeline_item(round_event, True)["summary"]

    encode_event = {
        "event_type": "encode_completed",
        "payload": {
            "title": "Example 2026",
            "crf": 21.4,
            "video_bitrate": 25_500_000,
            "source_video_bitrate": 75_000_000,
        },
    }
    assert "视频码率：25.50 Mbps（源码率的 34.0%）" in service.event_text(encode_event)

    percent_only_event = {
        "event_type": "quick_test_completed",
        "payload": {"title": "Legacy", "crf": 20, "bitrate_percent": 28.2},
    }
    percent_text = service.event_text(percent_only_event)
    assert "视频码率：源码率的 28.2%（缺少源码率，无法换算 Mbps）" in percent_text


def test_quick_test_notification_never_renders_empty_percent_placeholders():
    service = load_notification_service().NotificationService
    event = {
        "event_type": "quick_test_round_completed",
        "payload": {
            "title": "20th.Century.Women.2016",
            "encoder": "x265_10bit",
            "round": 3,
            "crf": 21.8,
            "sample_bitrate": 18_750_000,
            "source_bitrate_mbps": 62.5,
            "target_min_mbps": 17.0,
            "target_max_mbps": 20.0,
            "denoise_preset": "关闭",
            "sample_duration": 30,
        },
    }
    text = service.event_text(event)
    assert "视频码率：18.75 Mbps（源码率的 30.0%）" in text
    assert "目标区间：17.0 - 20.0 Mbps（源码率的 27.2% - 32.0%）" in text
    assert "-%" not in text

    legacy = dict(event)
    legacy["payload"] = {
        **event["payload"],
        "sample_bitrate": None,
        "target_min_mbps": None,
        "target_max_mbps": None,
        "bitrate_percent": 30.0,
        "target_min": 27.2,
        "target_max": 32.0,
    }
    legacy_text = service.event_text(legacy)
    assert "视频码率：18.75 Mbps（源码率的 30.0%）" in legacy_text
    assert "目标区间：17.0 - 20.0 Mbps（源码率的 27.2% - 32.0%）" in legacy_text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: ok")
