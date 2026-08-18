"""服务端固定配置。"""

import os
from pathlib import Path


OPENAI_API_KEY = ""
# 免费 ChatGPT 账号由 Codex 自动选择当前可用模型。
CODEX_MODEL: str | None = None
CODEX_REASONING_EFFORT = "low"
# Qoder 影响分析默认 skill（位于 ~/.qoder/skills/<名称>/SKILL.md）
QODER_SKILL_NAME = "requirement-impact-analysis"
# Qoder 模型 ID；None 表示使用 Qoder 默认模型
QODER_MODEL: str | None = None
# Qoder skills 根目录（包含各 skill 子目录）；为空时依次查找
# 项目内 qoder-skills/skills 和用户级 ~/.qoder/skills
QODER_SKILLS_DIR = os.environ.get("QODER_SKILLS_DIR", "")
ANALYSIS_CACHE_DIR = Path(__file__).resolve().parent / ".analysis-cache"
PORT = 8000
