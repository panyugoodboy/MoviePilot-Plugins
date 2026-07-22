import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins.v2" / "embylibrarydownload"


def test_manifest_matches_plugin_version_and_name():
    manifest = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))
    source = (PLUGIN / "__init__.py").read_text(encoding="utf-8")
    entry = manifest["EmbyLibraryDownload"]

    assert entry["name"] == "联动EMBY库筛选下载"
    assert entry["version"] == "0.2.1"
    assert entry["release"] is True
    assert 'plugin_version = "0.2.1"' in source
    assert 'plugin_icon = "emby.png"' in source


def test_remote_entry_references_existing_build_assets():
    assets = PLUGIN / "dist" / "assets"
    remote = (assets / "remoteEntry.js").read_text(encoding="utf-8")
    references = {
        value for value in re.findall(r'["\']\./([^"\']+)["\']', remote)
        if value.endswith((".js", ".css"))
    }

    assert references
    assert all((assets / reference).is_file() for reference in references)
    assert "./AppPage" in remote
    assert "./Config" in remote
