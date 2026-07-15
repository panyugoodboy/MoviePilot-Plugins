import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_CLIENT_PATH = ROOT / "plugins.v2" / "ubencodehelper" / "api_client.py"


def load_api_client():
    spec = importlib.util.spec_from_file_location("ubencodehelper_api_client_test", API_CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_plugin_registers_multi_client_buttons_and_two_stage_publish():
    source = (ROOT / "plugins.v2" / "ubencodehelper" / "__init__.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))

    assert "EventType.MessageAction" in source
    assert "target_device_id" in source
    assert "generate_screenshots" in source
    assert "prepare_publish" in source
    assert "confirm_publish" in source
    assert "confirm_token" in source
    assert manifest["UBencodeHelper"]["version"] == "1.3.0"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: ok")
