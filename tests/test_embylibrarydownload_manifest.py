import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins.v2" / "embylibrarydownload"


def test_manifest_matches_plugin_version_and_name():
    manifest = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))
    source = (PLUGIN / "__init__.py").read_text(encoding="utf-8")
    service = (PLUGIN / "service.py").read_text(encoding="utf-8")
    entry = manifest["EmbyLibraryDownload"]

    assert entry["name"] == "联动EMBY库筛选下载"
    assert entry["version"] == "0.3.5"
    assert entry["release"] is True
    assert 'plugin_version = "0.3.5"' in source
    assert '"auto_download_cron": ""' in source
    assert '"proxy_enabled": True' in source
    assert 'plugin_icon = "emby.png"' in source

    page = (ROOT / "frontend" / "embylibrarydownload" / "src" / "AppPage.vue").read_text(encoding="utf-8")
    assert "quickSearchTarget(target)" in page
    assert "target.inventory_state === 'present'" in page
    assert "tab.value = 'pool'" in page
    assert "正在快速搜索目标站点资源" in page
    assert 'self._search_targets, target_ids, False' in source
    assert 'process_target_from_pool' in source
    assert 'target-pool:' in source
    assert '选择目标榜单' in page
    assert '将整个榜单设为目标' in page
    assert 'target.items || [target]' in page
    assert 'mdi-check-circle' in page
    assert 'auto_download: true, prefer_scanned_pool: true' in page
    assert '"notify_enabled": True' in source
    assert '"notify_inventory": True' in source
    assert '"notify_targets": True' in source
    assert '"notify_pool": True' in source
    assert '"notify_download": True' in source
    assert '"notify_failures": True' in source
    assert '汇总通知已开启' in page
    assert '发送测试通知' in page
    assert '/notifications/test' in source
    assert 'class ProxySearchChain(SearchChain)' in service
    assert 'with_site_proxy(site, self.proxy_enabled)' in service
    assert 'search = self._search_chain()' in service
    assert 'torrent.site_proxy = bool(config.get("proxy_enabled"))' in service
    assert '站点请求全程使用代理' in page
    assert '/jobs/delete' in source
    assert '/jobs/retry' in source
    assert '/jobs/retry-failed' in source
    assert 'v-model="selectedJobs" show-select item-value="id"' in page
    assert '全部重试失败任务' in page
    assert 'statusLabel(item.status)' in page
    assert "failed: '失败'" in page
    assert 'cleanup_obsolete_failed_jobs' in source
    assert 'class="page-brand"' in page
    assert '--surface-soft:' in page
    assert '@media (max-width: 390px)' in page
    assert '@media (prefers-reduced-motion: reduce)' in page


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
