import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = [
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/CONTRACTS.md",
    "docs/DECISIONS.md",
    "docs/ROADMAP.md",
    "docs/JOURNAL.md",
    "docs/STATUS.md",
]


def test_docs_exist_and_are_non_empty():
    for relative in REQUIRED_DOCS:
        path = ROOT / relative
        assert path.is_file(), f"missing {relative}"
        assert path.read_text(encoding="utf-8").strip(), f"empty {relative}"


def test_every_plugin_has_manifest_and_readme():
    for plugin_dir in sorted((ROOT / "plugins").iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest = plugin_dir / "plugin.json"
        assert manifest.is_file(), f"{plugin_dir.name} missing plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["name"] == plugin_dir.name
        assert data["api_version"] == 1
        assert data["version"]
        assert data["entry"]
        assert (plugin_dir / "README.md").is_file(), f"{plugin_dir.name} missing README.md"


def test_agents_md_points_at_status():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/STATUS.md" in agents
    assert "app.kernel.api" in agents
