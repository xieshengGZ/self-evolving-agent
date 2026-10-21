import json

import pytest

from app.kernel.bus import EventBus
from app.kernel.plugins import PluginLoadError, PluginManager
from app.kernel.store import Store


PLUGIN_BODY = """
from app.kernel.api import PluginContext

def setup(ctx: PluginContext) -> None:
    ctx.tools.register("ping", lambda payload: {{"version": "{version}"}})
    ctx.http.register("ping", lambda payload: {{"version": "{version}"}})
"""

BROKEN_BODY = """
def setup(ctx):
    raise RuntimeError("boom")
"""


def write_plugin(root, name, version, body):
    directory = root / name
    directory.mkdir(exist_ok=True)
    (directory / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "api_version": 1,
                "entry": "plugin.py",
                "status": "active",
            }
        ),
        encoding="utf-8",
    )
    (directory / "plugin.py").write_text(body, encoding="utf-8")


@pytest.fixture
def manager(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    store = Store()
    return PluginManager(plugins_dir, store, EventBus(store))


def test_load_and_execute(manager):
    write_plugin(manager.plugins_dir, "demo", "0.1.0", PLUGIN_BODY.format(version="v1"))
    manager.load_all()

    assert manager.tool_names() == ["ping"]
    assert manager.execute_tool("ping", {}) == {"version": "v1"}
    assert manager.get_http("demo", "ping")({}) == {"version": "v1"}
    assert [p["name"] for p in manager.list_plugins()] == ["demo"]


def test_reload_swaps_version_without_restart(manager):
    write_plugin(manager.plugins_dir, "demo", "0.1.0", PLUGIN_BODY.format(version="v1"))
    manager.load_all()

    write_plugin(manager.plugins_dir, "demo", "0.2.0", PLUGIN_BODY.format(version="v2"))
    loaded = manager.reload("demo")

    assert loaded.manifest.version == "0.2.0"
    assert manager.execute_tool("ping", {}) == {"version": "v2"}


def test_failed_reload_keeps_old_version(manager):
    write_plugin(manager.plugins_dir, "demo", "0.1.0", PLUGIN_BODY.format(version="v1"))
    manager.load_all()

    (manager.plugins_dir / "demo" / "plugin.py").write_text(BROKEN_BODY, encoding="utf-8")
    with pytest.raises(PluginLoadError):
        manager.reload("demo")

    assert manager.execute_tool("ping", {}) == {"version": "v1"}


def test_bad_manifest_is_reported_not_fatal(manager):
    bad = manager.plugins_dir / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text("{not json", encoding="utf-8")

    manager.load_all()
    assert "bad" in manager.errors()
    assert manager.tool_names() == []
