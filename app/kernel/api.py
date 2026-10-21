"""Public plugin API. Plugins should import only from this module."""
from app.kernel.context import PluginContext

__all__ = ["PluginContext"]
