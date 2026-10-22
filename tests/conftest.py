import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="agent-services-tests-"))
(_TEST_ROOT / "plugins").mkdir()
(_TEST_ROOT / "prompts").mkdir()

os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["AGENT_WEB_TOKEN"] = "test-token"
os.environ["AGENT_ROOT"] = str(_TEST_ROOT)
os.environ["AGENT_PLUGIN_AUTORELOAD"] = "0"
