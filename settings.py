"""服务端固定配置。"""

from pathlib import Path


OPENAI_API_KEY = ""
# 免费 ChatGPT 账号由 Codex 自动选择当前可用模型。
CODEX_MODEL: str | None = None
CODEX_REASONING_EFFORT = "low"
ANALYSIS_CACHE_DIR = Path(__file__).resolve().parent / ".analysis-cache"
PORT = 8000
