import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.kernel.http import register_plugin_routes
from app.kernel.runtime import KernelRuntime


PLUGIN = """
from app.kernel.api import PluginContext

def setup(ctx: PluginContext) -> None:
    ctx.tools.register("ping", lambda payload: {"reply": ctx.config.get("reply", "pong")})
    ctx.http.register("ping", lambda payload: {"reply": ctx.config.get("reply", "pong"), "echo": payload.get("echo")})
"""


def make_plugin(root, version="0.1.0", reply="pong"):
    plugin_dir = root / "hello"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "hello",
                "version": version,
                "api_version": 1,
                "entry": "plugin.py",
                "status": "experimental",
                "config": {"reply": reply},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(PLUGIN, encoding="utf-8")


def build_client(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    make_plugin(plugins_dir)
    runtime = KernelRuntime(plugins_dir=plugins_dir)
    runtime.start(announce=False)

    app = FastAPI()
    register_plugin_routes(app, runtime, lambda: None)
    return TestClient(app), runtime


def test_plugin_http_op_and_listing(tmp_path):
    client, _ = build_client(tmp_path)

    response = client.post("/api/plugin/hello/ping", json={"echo": "hi"})
    assert response.status_code == 200
    assert response.json() == {"reply": "pong", "echo": "hi"}

    listing = client.get("/api/plugins").json()
    assert listing["ok"] is True
    assert [p["name"] for p in listing["plugins"]] == ["hello"]
    assert listing["tools"] == ["ping"]


def test_unknown_plugin_op_returns_404(tmp_path):
    client, _ = build_client(tmp_path)
    assert client.post("/api/plugin/hello/nope", json={}).status_code == 404
    assert client.post("/api/plugin/missing/ping", json={}).status_code == 404


def test_reload_endpoint_picks_up_new_config(tmp_path):
    client, runtime = build_client(tmp_path)

    make_plugin(runtime.plugins.plugins_dir, version="0.2.0", reply="pong2")
    reload_response = client.post("/api/plugins/hello/reload")
    assert reload_response.status_code == 200
    assert reload_response.json()["version"] == "0.2.0"

    response = client.post("/api/plugin/hello/ping", json={})
    assert response.json()["reply"] == "pong2"
