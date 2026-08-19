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
# Qoder 个人访问令牌（https://qoder.com/account/integrations 生成）；
# 可直接在下方引号内填写固定值，也可用环境变量 QODER_PERSONAL_ACCESS_TOKEN 覆盖；
# 两者均为空时回退本机 Qoder CLI 登录态（~/.qoder/.auth）
_QODER_TOKEN_FIXED = ""
QODER_PERSONAL_ACCESS_TOKEN = os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", "") or _QODER_TOKEN_FIXED
# Qoder skills 根目录（包含各 skill 子目录）；为空时依次查找
# 项目内 qoder-skills/skills 和用户级 ~/.qoder/skills
QODER_SKILLS_DIR = os.environ.get("QODER_SKILLS_DIR", "")
ANALYSIS_CACHE_DIR = Path(__file__).resolve().parent / ".analysis-cache"
PORT = 8000
