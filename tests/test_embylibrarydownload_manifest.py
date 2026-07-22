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
    assert entry["version"] == "0.2.8"
    assert entry["release"] is True
    assert 'plugin_version = "0.2.8"' in source
    assert '"auto_download_cron": ""' in source
    assert 'plugin_icon = "emby.png"' in source

    page = (ROOT / "frontend" / "embylibrarydownload" / "src" / "AppPage.vue").read_text(encoding="utf-8")
    assert "quickSearchTarget(target)" in page
    assert "target.inventory_state === 'present'" in page
    assert "tab.value = 'pool'" in page
    assert "正在快速搜索目标站点资源" in page
    assert 'self._search_targets, target_ids, False' in source
    assert 'process_target_from_pool' in source
    assert 'target-pool:' in source
    assert '从自定义推荐选择' in page
    assert 'target.poster_url' in page
    assert 'mdi-check-circle' in page
    assert 'auto_download: true, prefer_scanned_pool: true' in page


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
