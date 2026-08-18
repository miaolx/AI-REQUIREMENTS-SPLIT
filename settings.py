"""服务端固定配置。"""

from pathlib import Path


OPENAI_API_KEY = ""
CODEX_MODEL = "gpt-5.4-mini"
CODEX_REASONING_EFFORT = "high"
ANALYSIS_CACHE_DIR = Path(__file__).resolve().parent / ".analysis-cache"
PORT = 8000
