import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins.v2" / "mporganizecorrect"


def test_manifest_matches_plugin_version_and_safety_contract():
    manifest = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))
    source = (PLUGIN / "__init__.py").read_text(encoding="utf-8")
    service = (PLUGIN / "service.py").read_text(encoding="utf-8")
    page = (ROOT / "frontend" / "mporganizecorrect" / "src" / "AppPage.vue").read_text(encoding="utf-8")
    entry = manifest["MPOrganizeCorrect"]

    assert entry["name"] == "MP整理纠正"
    assert entry["version"] == "1.0.3"
    assert entry["release"] is True
    assert 'plugin_version = "1.0.3"' in source
    assert '"scan_cron": "0 4 * * *"' in source
    assert '"auto_correct": False' in source
    assert '"cleanup_old_after_correct": True' in source
    assert 'if value == "move"' in (PLUGIN / "matcher.py").read_text(encoding="utf-8")
    assert "cleanup_paths_are_safe" in service
    assert "_restore_history(snapshot)" in service
    assert "源文件永久保留" in page
    assert "source_safe_confirmed" in page
    assert "已逐条完成" in page
    assert '"/records/correct-all"' in source
    assert "if len(items) > 10" not in source
    assert "一键全部纠正" in page
    assert "批量纠正最多 10 条" not in page
    assert "年份（可选）" in page
    assert "模糊搜索" in page
    assert "sourceFileName" in page
    assert "@media(max-width:390px)" in page
    assert "@media(prefers-reduced-motion:reduce)" in page


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
