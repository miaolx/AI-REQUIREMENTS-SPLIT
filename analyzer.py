"""
核心分析模块：基于 Qoder Agent SDK 封装需求拆分与代码影响分析逻辑。

使用 requirement-impact-analysis skill 对指定项目代码进行影响范围分析。
"""

import asyncio
import os
import queue
import threading
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from qoder_agent_sdk import (
    QoderAgentOptions,
    access_token_from_env,
    qodercli_auth,
    query,
)
from qoder_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)

# ── 默认配置 ──────────────────────────────────────────────

DEFAULT_SKILL_NAME = "requirement-impact-analysis"

# 分析所需工具白名单（只读 + 搜索 + 技能调用）
DEFAULT_ALLOWED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Agent",
    "Skill",
    "List",
    "LSP",
]


@dataclass
class AnalysisConfig:
    """分析任务配置。"""

    prd_url: str                     # 产品文档 / 需求地址 URL
    project_path: str                # 待分析代码项目根目录
    prototype_image: Optional[str] = None  # 原型图文件路径（可选）
    skill_name: str = DEFAULT_SKILL_NAME
    skill_path: Optional[str] = None  # 自定义 skill 目录路径（可选）
    extra_context: str = ""           # 额外项目上下文（可选）
    model: Optional[str] = None       # 模型别名(如 sonnet/opus/haiku)或完整模型 ID（可选，默认用 CLI 默认模型）


@dataclass
class AnalysisEvent:
    """分析过程中的流式事件。"""

    event_type: str   # "text" | "tool" | "system" | "result" | "error"
    content: str
    metadata: dict = field(default_factory=dict)


# ── 内部工具 ──────────────────────────────────────


def _run_query_in_thread(prompt: str, options, out_queue: "queue.Queue") -> None:
    """
    在工作线程中运行 SDK query。

    背景：uvicorn 在 Windows + reload=True 下只能用 SelectorEventLoop（否则
    继承的监听 socket 注册 IOCP 报 WinError 87），而 SelectorEventLoop 又无法
    启动 qodercli 子进程。因此把 SDK 调用放到独立线程，线程内 asyncio.run
    使用 Windows 默认的 ProactorEventLoop，子进程能力正常。
    """

    async def _consume() -> None:
        async for message in query(prompt=prompt, options=options):
            out_queue.put(message)

    try:
        asyncio.run(_consume())
    except BaseException as e:  # noqa: BLE001
        out_queue.put(e)
    finally:
        out_queue.put(None)


def _build_prompt(config: AnalysisConfig) -> str:
    """根据配置构造发送给 Agent 的 prompt。"""
    parts = [
        f"请使用 {config.skill_name} skill 进行需求拆分与代码影响分析。",
        "",
        f"【需求地址】：{config.prd_url}",
    ]
    if config.prototype_image:
        parts.append(f"【原型图】：{config.prototype_image}")
    else:
        parts.append("【原型图】：无（仅根据需求文档分析）")
    parts += [
        "",
        "请按照 skill 工作流严格执行：",
        "1. 用 WebFetch/fetch_content 抓取需求地址内容",
    ]
    if config.prototype_image:
        parts.append("2. 用 Read/read_file 读取原型图图片")
    parts += [
        "3. 拆分最小粒度功能点清单",
        "4. 逐个功能点串行进行代码定位、修改范围判定、验证",
        "5. 最终给出影响面总结报告",
    ]
    if config.extra_context:
        parts.append("")
        parts.append(f"【项目上下文】：{config.extra_context}")
    return "\n".join(parts)


def _get_auth():
    """自动选择认证方式：优先 PAT 环境变量，其次本地 qodercli 登录态。"""
    if os.environ.get("QODER_PERSONAL_ACCESS_TOKEN"):
        return access_token_from_env()
    return qodercli_auth()


def _build_options(config: AnalysisConfig, stderr_callback=None) -> QoderAgentOptions:
    """根据配置构造 QoderAgentOptions。"""
    auth = _get_auth()

    # setting_sources 决定 CLI 从哪里发现 skills
    # 'user'  → ~/.qoder/skills/（用户级 skill）
    # 'project' → {cwd}/.qoder/skills/（项目级 skill）
    setting_sources = ["user"]
    if config.skill_path:
        setting_sources.append("project")

    return QoderAgentOptions(
        auth=auth,
        cwd=config.project_path,
        setting_sources=setting_sources,
        skills=[config.skill_name],
        permission_mode="acceptEdits",
        allowed_tools=DEFAULT_ALLOWED_TOOLS,
        include_partial_messages=True,
        model=config.model,
        stderr=stderr_callback,
    )


# ── 公开接口 ──────────────────────────────────────────────


async def stream_analysis(
    config: AnalysisConfig,
) -> AsyncGenerator[AnalysisEvent, None]:
    """
    流式执行需求影响分析，逐条 yield 事件。

    事件类型:
      - "text":    Agent 输出的文本内容
      - "tool":     Agent 调用的工具
      - "system":   系统消息（初始化等）
      - "result":   最终结果
      - "error":    错误
    """
    prompt = _build_prompt(config)

    # 捕获 qodercli 的 stderr，便于定位 CLI 退出码非 0 的真实原因
    stderr_lines: list[str] = []

    def _on_stderr(line: str) -> None:
        stderr_lines.append(line)
        print(f"[qodercli stderr] {line}", flush=True)

    options = _build_options(config, stderr_callback=_on_stderr)

    out_queue: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_query_in_thread,
        args=(prompt, options, out_queue),
        daemon=True,
    )
    thread.start()

    loop = asyncio.get_running_loop()
    try:
        while True:
            item = await loop.run_in_executor(None, out_queue.get)
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            message = item

            # ── Assistant 消息：文本 + 工具调用 ──
            if isinstance(message, AssistantMessage):
                for block in (message.content or []):
                    if isinstance(block, TextBlock):
                        if block.text:
                            yield AnalysisEvent("text", block.text)
                    elif isinstance(block, ToolUseBlock):
                        yield AnalysisEvent(
                            "tool",
                            f"调用工具: {block.name}",
                            {"tool": block.name, "input": block.input},
                        )

            # ── System 消息 ──
            elif isinstance(message, SystemMessage):
                yield AnalysisEvent("system", f"系统消息: {message.subtype}")

            # ── Result 消息 ──
            elif isinstance(message, ResultMessage):
                if message.subtype == "success":
                    yield AnalysisEvent(
                        "result",
                        message.result or "分析完成",
                        {"subtype": message.subtype},
                    )
                else:
                    yield AnalysisEvent(
                        "result",
                        f"分析结束: {message.subtype}",
                        {"subtype": message.subtype, "result": message.result},
                    )

            # ── Stream 事件（增量内容） ──
            elif isinstance(message, StreamEvent):
                yield AnalysisEvent(
                    "stream",
                    str(message.event),
                    {"event": message.event},
                )

    except Exception as e:
        detail = str(e)
        if stderr_lines:
            detail += "\n[qodercli stderr 最后 20 行]\n" + "\n".join(stderr_lines[-20:])
        yield AnalysisEvent("error", f"分析过程中出错: {detail}", {"exception": str(e)})
        raise


async def run_analysis(config: AnalysisConfig) -> str:
    """
    执行完整分析并返回最终报告文本（阻塞直到完成）。
    """
    texts: list[str] = []

    async for event in stream_analysis(config):
        if event.event_type == "text":
            texts.append(event.content)
        elif event.event_type == "error":
            raise RuntimeError(event.content)

    return "\n".join(texts)
