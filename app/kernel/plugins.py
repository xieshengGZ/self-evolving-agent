"""Plugin discovery, loading, and atomic hot replacement.

A plugin is a directory with `plugin.json` and an entry module exposing
`setup(ctx)`. Loading builds a fresh RegistrySet, so a failed reload never
corrupts the version that is currently serving traffic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.kernel.bus import EventBus
from app.kernel.context import PluginContext
from app.kernel.registry import RegistrySet
from app.kernel.store import Store

log = logging.getLogger("kernel.plugins")

API_VERSION = 1


class PluginLoadError(RuntimeError):
    pass


@dataclass
class PluginManifest:
    name: str
    version: str
    api_version: int
    entry: str
    status: str = "active"
    description: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    path: Path = field(default_factory=Path)

    @classmethod
    def load(cls, manifest_path: Path) -> "PluginManifest":
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise PluginLoadError(f"invalid plugin.json: {exc}") from exc
        if not isinstance(data, dict):
            raise PluginLoadError("plugin.json must be a JSON object")

        name = str(data.get("name") or "").strip()
        if not name:
            raise PluginLoadError("missing name")
        if name != manifest_path.parent.name:
            raise PluginLoadError(
                f"name mismatch: manifest={name!r} directory={manifest_path.parent.name!r}"
            )

        version = str(data.get("version") or "").strip()
        if not version:
            raise PluginLoadError("missing version")

        try:
            api_version = int(data.get("api_version", 0))
        except (TypeError, ValueError) as exc:
            raise PluginLoadError("api_version must be an integer") from exc
        if api_version != API_VERSION:
            raise PluginLoadError(
                f"unsupported api_version {api_version}; expected {API_VERSION}"
            )

        entry = str(data.get("entry") or "plugin.py").strip()
        if not entry:
            raise PluginLoadError("missing entry")

        config = data.get("config") or {}
        if not isinstance(config, dict):
            raise PluginLoadError("config must be a JSON object")

        return cls(
            name=name,
            version=version,
            api_version=api_version,
            entry=entry,
            status=str(data.get("status") or "active"),
            description=str(data.get("description") or ""),
            config=config,
            path=manifest_path.parent,
        )


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: Any
    layer: RegistrySet
    ctx: PluginContext
    teardown: Optional[Callable[[PluginContext], None]]
    module_name: str
    loaded_at: float


class PluginManager:
    def __init__(self, plugins_dir: str | os.PathLike[str], store: Store, bus: EventBus) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.store = store
        self.bus = bus
        self._plugins: Dict[str, LoadedPlugin] = {}
        self._errors: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._watcher: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---------------- discovery ----------------
    def discover(self) -> List[PluginManifest]:
        if not self.plugins_dir.is_dir():
            return []
        manifests: List[PluginManifest] = []
        for child in sorted(self.plugins_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.is_file():
                continue
            try:
                manifests.append(PluginManifest.load(manifest_path))
            except Exception as exc:  # noqa: BLE001
                self._errors[child.name] = f"manifest: {exc}"
                log.warning("bad plugin manifest in %s: %s", child, exc)
        return manifests

    def _manifest(self, name: str) -> PluginManifest:
        for manifest in self.discover():
            if manifest.name == name:
                return manifest
        raise PluginLoadError(f"plugin not found: {name}")

    # ---------------- loading ----------------
    def load_all(self) -> None:
        for manifest in self.discover():
            if manifest.status == "disabled":
                continue
            try:
                loaded = self._build(manifest)
            except Exception as exc:  # noqa: BLE001
                self._errors[manifest.name] = f"{type(exc).__name__}: {exc}"
                log.exception("failed to load plugin %s", manifest.name)
                continue
            self._install(loaded)
            log.info("loaded plugin %s %s", loaded.manifest.name, loaded.manifest.version)

    def reload(self, name: str) -> LoadedPlugin:
        manifest = self._manifest(name)
        loaded = self._build(manifest)
        with self._lock:
            old = self._plugins.get(name)
            self._plugins[name] = loaded
            self._errors.pop(name, None)
        if old is not None:
            self._safe_teardown(old)
        self.bus.publish("plugin.reloaded", {"name": name, "version": manifest.version})
        log.info("reloaded plugin %s %s", name, manifest.version)
        return loaded

    def unload(self, name: str) -> bool:
        with self._lock:
            loaded = self._plugins.pop(name, None)
        if loaded is None:
            return False
        self._safe_teardown(loaded)
        self.bus.publish("plugin.unloaded", {"name": name})
        return True

    def _install(self, loaded: LoadedPlugin) -> None:
        with self._lock:
            old = self._plugins.get(loaded.manifest.name)
            self._plugins[loaded.manifest.name] = loaded
            self._errors.pop(loaded.manifest.name, None)
        if old is not None:
            self._safe_teardown(old)
        self.bus.publish("plugin.loaded", {"name": loaded.manifest.name, "version": loaded.manifest.version})

    def _build(self, manifest: PluginManifest) -> LoadedPlugin:
        root = manifest.path.resolve()
        entry = (root / manifest.entry).resolve()
        if root not in entry.parents:
            raise PluginLoadError(f"entry escapes plugin directory: {manifest.entry}")
        if not entry.is_file():
            raise PluginLoadError(f"entry not found: {entry}")

        module_name = self._module_name(manifest.name, entry)
        module = types.ModuleType(module_name)
        module.__file__ = str(entry)
        module.__package__ = ""
        sys.modules[module_name] = module

        try:
            # Compile from source instead of importlib: plugin files are often
            # rewritten within the same second, and stale .pyc reuse would
            # silently serve the old version after a hot reload.
            code = compile(entry.read_bytes(), str(entry), "exec")
            exec(code, module.__dict__)  # noqa: S102
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise PluginLoadError("entry module must define setup(ctx)")

            layer = RegistrySet()
            ctx = PluginContext(
                name=manifest.name,
                version=manifest.version,
                config=dict(manifest.config),
                layer=layer,
                store=self.store,
                bus=self.bus,
                log=logging.getLogger(f"plugin.{manifest.name}"),
            )
            setup(ctx)

            healthcheck = getattr(module, "healthcheck", None)
            if callable(healthcheck):
                healthy = healthcheck(ctx)
                if healthy is False:
                    raise PluginLoadError("healthcheck returned False")
        except PluginLoadError:
            sys.modules.pop(module_name, None)
            raise
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise PluginLoadError(f"{type(exc).__name__}: {exc}") from exc

        teardown = getattr(module, "teardown", None)
        return LoadedPlugin(
            manifest=manifest,
            module=module,
            layer=layer,
            ctx=ctx,
            teardown=teardown if callable(teardown) else None,
            module_name=module_name,
            loaded_at=time.time(),
        )

    @staticmethod
    def _module_name(name: str, entry: Path) -> str:
        digest = hashlib.sha1(entry.read_bytes()).hexdigest()[:12]
        safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
        return f"_agent_plugin_{safe}_{digest}"

    def _safe_teardown(self, loaded: LoadedPlugin) -> None:
        if loaded.teardown is not None:
            try:
                loaded.teardown(loaded.ctx)
            except Exception:  # noqa: BLE001
                log.exception("teardown failed for plugin %s", loaded.manifest.name)
        if sys.modules.get(loaded.module_name) is loaded.module:
            sys.modules.pop(loaded.module_name, None)

    # ---------------- lookup ----------------
    def get(self, name: str) -> Optional[LoadedPlugin]:
        with self._lock:
            return self._plugins.get(name)

    def list_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            plugins = list(self._plugins.values())
        return [
            {
                "name": loaded.manifest.name,
                "version": loaded.manifest.version,
                "status": loaded.manifest.status,
                "description": loaded.manifest.description,
                "tools": loaded.layer.tools.names(),
                "http": loaded.layer.http.ops(),
                "planners": loaded.layer.planners.names(),
                "hooks": loaded.layer.hooks.describe(),
                "loaded_at": loaded.loaded_at,
            }
            for loaded in plugins
        ]

    def tool_names(self) -> List[str]:
        names: List[str] = []
        with self._lock:
            plugins = list(self._plugins.values())
        for loaded in plugins:
            for name in loaded.layer.tools.names():
                if name not in names:
                    names.append(name)
        return names

    def execute_tool(self, name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            plugins = list(self._plugins.values())
        for loaded in reversed(plugins):
            if loaded.layer.tools.has(name):
                return loaded.layer.tools.execute(name, payload or {})
        return {"error": f"unknown action: {name}"}

    def get_http(self, plugin: str, op: str) -> Optional[Callable[[Dict[str, Any]], Any]]:
        loaded = self.get(plugin)
        if loaded is None:
            return None
        return loaded.layer.http.get(op)

    def get_planner(self, name: str = "default") -> Any:
        with self._lock:
            plugins = list(self._plugins.values())
        for loaded in reversed(plugins):
            planner = loaded.layer.planners.get(name)
            if planner is not None:
                return planner
        return None

    def context_builders(self) -> List[tuple[int, str, Any]]:
        with self._lock:
            plugins = list(self._plugins.values())
        builders: List[tuple[int, str, Any]] = []
        for loaded in plugins:
            for order, builder in loaded.layer.context.entries():
                builders.append((order, loaded.manifest.name, builder))
        return sorted(builders, key=lambda item: item[0])

    def planner_names(self) -> List[str]:
        names: List[str] = []
        with self._lock:
            plugins = list(self._plugins.values())
        for loaded in plugins:
            for name in loaded.layer.planners.names():
                if name not in names:
                    names.append(name)
        return names

    def emit_hook(self, event: str, **kwargs: Any) -> List[Any]:
        results: List[Any] = []
        with self._lock:
            plugins = list(self._plugins.values())
        for loaded in plugins:
            results.extend(loaded.layer.hooks.emit(event, **kwargs))
        return results

    def errors(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._errors)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "plugins": self.list_plugins(),
            "tools": self.tool_names(),
            "planners": self.planner_names(),
            "context_builders": [name for _, name, _ in self.context_builders()],
            "errors": self.errors(),
        }

    # ---------------- watcher ----------------
    def start_watcher(self, interval: float = 2.0) -> None:
        if self._watcher is not None:
            return
        self._stop.clear()
        self._watcher = threading.Thread(
            target=self._watch_loop, args=(interval,), name="plugin-watcher", daemon=True
        )
        self._watcher.start()

    def stop_watcher(self) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=5)
            self._watcher = None

    def _watch_loop(self, interval: float) -> None:
        seen: Dict[str, tuple[str, float]] = {}
        while not self._stop.wait(interval):
            for manifest in self.discover():
                if manifest.status == "disabled":
                    continue
                entry = manifest.path / manifest.entry
                try:
                    key = (str(entry), entry.stat().st_mtime)
                except OSError:
                    continue
                if manifest.name in seen and seen[manifest.name] != key:
                    try:
                        self.reload(manifest.name)
                    except Exception:  # noqa: BLE001
                        log.exception("auto reload failed for %s", manifest.name)
                seen[manifest.name] = key
