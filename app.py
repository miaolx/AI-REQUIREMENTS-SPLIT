"""需求影响分析服务：FastAPI + OpenAI Codex Python SDK。"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from analyzer import DEFAULT_MODEL, AnalysisConfig, run_analysis, stream_analysis
from qoder_analyzer import (
    DEFAULT_QODER_MODEL,
    DEFAULT_QODER_SKILL_NAME,
    QoderAnalysisConfig,
    run_qoder_analysis,
    stream_qoder_analysis,
)
from settings import PORT


REQUIRED_PROJECT_BRANCH = "dev"


class AnalyzeRequest(BaseModel):
    """影响范围分析请求。"""

    requirement_html_path: str = Field(..., description="本地需求产品文档绝对路径，支持 .html/.htm/.md")
    prototype_image: str | None = Field(
        default=None,
        description="原型图本地绝对路径或 http/https URL；URL 会先下载到临时文件",
    )
    project_path: str = Field(
        ...,
        description="待分析本地 Git 仓库绝对路径；仓库当前分支必须为 dev",
    )
    skill_name: str = Field(
        default="requirement-impact-analyzer",
        description="Codex skill 名称",
    )
    skill_path: str | None = Field(
        default=None,
        description="SKILL.md 或 skill 目录绝对路径；为空时使用 ~/.codex/skills/<skill_name>/SKILL.md",
    )
    extra_context: str = Field(default="", description="额外项目背景，仅作为不可信分析证据")
    model: str | None = Field(
        default=None,
        description=f"Codex 模型 ID；为空时使用服务端固定模型 {DEFAULT_MODEL}",
    )
    refresh_cache: bool = Field(default=False, description="是否忽略相同输入的已有分析缓存并重新分析")


class AnalyzeResponse(BaseModel):
    """同步分析响应，report 为精简的 Markdown 文件变更清单。"""

    success: bool
    report: str = ""
    error: str = ""


class QoderAnalyzeRequest(BaseModel):
    """Qoder 引擎影响范围分析请求。"""

    prototype_image: str | None = Field(
        default=None,
        description="原型图本地绝对路径或 http/https URL；URL 会先下载到临时文件；可为空（仅依据需求文档分析）",
    )
    project_path: str = Field(
        ...,
        description="待分析本地 Git 仓库绝对路径；仓库当前分支必须为 dev",
    )
    requirement_html_path: str | None = Field(
        default=None,
        description="可选：本地需求产品文档绝对路径，支持 .html/.htm/.md",
    )
    skill_name: str = Field(
        default=DEFAULT_QODER_SKILL_NAME,
        description="Qoder skill 名称（位于 ~/.qoder/skills/<名称>）",
    )
    extra_context: str = Field(default="", description="额外项目背景，仅作为不可信分析证据")
    model: str | None = Field(
        default=None,
        description="Qoder 模型 ID；为空时使用 Qoder 默认模型",
    )
    refresh_cache: bool = Field(default=False, description="是否忽略相同输入的已有分析缓存并重新分析")


def _decode_json_lenient(text: str):
    """兼容标准 JSON、尾逗号和 Python 字典字面量。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    try:
        value = ast.literal_eval(repaired)
        if isinstance(value, dict):
            return value
    except (ValueError, SyntaxError, MemoryError):
        pass

    raise HTTPException(
        status_code=400,
        detail=(
            "请求体不是合法 JSON。属性名/字符串请使用双引号；Windows 路径中的反斜杠"
            "需双写转义(\\\\)，或直接使用正斜杠(/)。"
        ),
    )


class TolerantRequest(Request):
    """为现有接口保留宽松 JSON 解析行为。"""

    async def json(self):
        raw = await self.body()
        text = raw.decode("utf-8-sig").strip()
        if not text:
            raise HTTPException(status_code=400, detail="请求体为空")
        return _decode_json_lenient(text)


class TolerantRoute(APIRoute):
    """将默认 Request 替换为 TolerantRequest。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def custom_route_handler(request: Request):
            request.__class__ = TolerantRequest
            return await original(request)

        return custom_route_handler


app = FastAPI(
    title="需求影响分析服务",
    description="通过 OpenAI Codex Python SDK 和 requirement-impact-analyzer skill 分析代码影响范围",
    version="2.1.0",
)
app.router.route_class = TolerantRoute


@app.get("/health")
async def health():
    return {"status": "ok", "engine": "openai-codex", "default_model": DEFAULT_MODEL}


@dataclass(slots=True)
class ResolvedPrototype:
    path: str | None
    temporary: bool = False

    def cleanup(self) -> None:
        if self.temporary and self.path:
            try:
                Path(self.path).unlink(missing_ok=True)
            except OSError:
                pass


def _run_git(project: Path, *args: str) -> str:
    """只读执行本地 Git 查询并返回标准输出。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="服务器未安装 Git 或 Git 不在 PATH 中") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=400, detail=f"读取本地 Git 仓库超时: {project}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "未知 Git 错误"
        raise HTTPException(status_code=400, detail=f"无法读取本地 Git 仓库 {project}: {detail}")
    return result.stdout.strip()


def _resolve_local_dev_repository(project: Path) -> Path:
    """解析本地仓库根目录，并强制当前分支为 dev。"""
    repository = Path(_run_git(project, "rev-parse", "--show-toplevel")).resolve()
    branch = _run_git(repository, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != REQUIRED_PROJECT_BRANCH:
        current = branch or "detached HEAD"
        raise HTTPException(
            status_code=400,
            detail=f"project_path 当前必须位于本地 dev 分支，实际分支: {current}",
        )
    return repository


def _validate_request(req: AnalyzeRequest) -> tuple[str, str]:
    """校验需求文档（HTML/Markdown），并将项目限制为本地 dev 分支仓库。"""
    requirement = Path(req.requirement_html_path).expanduser()
    if not requirement.is_absolute():
        raise HTTPException(status_code=400, detail="requirement_html_path 必须是绝对路径")
    requirement = requirement.resolve()
    if not requirement.is_file():
        raise HTTPException(status_code=400, detail=f"需求文档不存在: {requirement}")
    if requirement.suffix.lower() not in {".html", ".htm", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail="requirement_html_path 必须指向 .html/.htm/.md 文件")

    project = Path(req.project_path).expanduser()
    if not project.is_absolute():
        raise HTTPException(status_code=400, detail="project_path 必须是绝对路径")
    project = project.resolve()
    if not project.is_dir():
        raise HTTPException(status_code=400, detail=f"项目路径不存在或不是目录: {project}")
    project = _resolve_local_dev_repository(project)
    return str(requirement), str(project)


def _resolve_prototype_image(prototype_image: str | None) -> ResolvedPrototype:
    """将本地路径或 URL 解析为 Codex 可读取的本地图片。"""
    if not prototype_image:
        return ResolvedPrototype(None)

    if prototype_image.startswith(("http://", "https://")):
        suffix = Path(urlparse(prototype_image).path).suffix or ".png"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        try:
            request = urllib.request.Request(prototype_image, headers={"User-Agent": "codex-impact-analyzer/2.0"})
            with urllib.request.urlopen(request, timeout=30) as response, open(tmp.name, "wb") as output:
                remaining = 20 * 1024 * 1024
                while chunk := response.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("原型图超过 20 MiB 限制")
                    output.write(chunk)
        except Exception as exc:
            Path(tmp.name).unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"下载原型图失败: {exc}") from exc
        return ResolvedPrototype(tmp.name, temporary=True)

    prototype = Path(prototype_image).expanduser()
    if not prototype.is_absolute():
        raise HTTPException(status_code=400, detail="prototype_image 必须是绝对路径或 http/https URL")
    prototype = prototype.resolve()
    if not prototype.is_file():
        raise HTTPException(status_code=400, detail=f"原型图文件不存在: {prototype}")
    return ResolvedPrototype(str(prototype))


def _build_config(req: AnalyzeRequest, prototype_path: str | None) -> AnalysisConfig:
    requirement, project = _validate_request(req)
    return AnalysisConfig(
        requirement_html_path=requirement,
        prototype_image=prototype_path,
        project_path=project,
        skill_name=req.skill_name,
        skill_path=req.skill_path,
        extra_context=req.extra_context,
        model=req.model or DEFAULT_MODEL,
        refresh_cache=req.refresh_cache,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """执行只读分析，等待 Codex 返回精简的 Markdown 文件变更清单。"""
    resolved = _resolve_prototype_image(req.prototype_image)
    try:
        config = _build_config(req, resolved.path)
        report = await run_analysis(config)
        return AnalyzeResponse(success=True, report=report)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return AnalyzeResponse(success=False, error=str(exc))
    finally:
        resolved.cleanup()


@app.get("/api/analyze/stream")
async def analyze_stream(
    requirement_html_path: str,
    project_path: str,
    prototype_image: str | None = None,
    skill_name: str = "requirement-impact-analyzer",
    skill_path: str | None = None,
    extra_context: str = "",
    model: str | None = None,
    refresh_cache: bool = False,
):
    """以 SSE 推送启动状态以及最终文件变更清单。"""
    req = AnalyzeRequest(
        requirement_html_path=requirement_html_path,
        project_path=project_path,
        prototype_image=prototype_image,
        skill_name=skill_name,
        skill_path=skill_path,
        extra_context=extra_context,
        model=model,
        refresh_cache=refresh_cache,
    )
    requirement, project = _validate_request(req)
    resolved = _resolve_prototype_image(prototype_image)
    config = AnalysisConfig(
        requirement_html_path=requirement,
        project_path=project,
        prototype_image=resolved.path,
        skill_name=skill_name,
        skill_path=skill_path,
        extra_context=extra_context,
        model=model or DEFAULT_MODEL,
        refresh_cache=refresh_cache,
    )

    async def event_stream():
        try:
            async for event in stream_analysis(config):
                data = {"type": event.event_type, "content": event.content, "metadata": event.metadata}
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            resolved.cleanup()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _validate_qoder_request(req: QoderAnalyzeRequest) -> tuple[str | None, str]:
    """校验可选需求文档（HTML/Markdown），并将项目限制为本地 dev 分支仓库。"""
    requirement: str | None = None
    if req.requirement_html_path:
        candidate = Path(req.requirement_html_path).expanduser()
        if not candidate.is_absolute():
            raise HTTPException(status_code=400, detail="requirement_html_path 必须是绝对路径")
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail=f"需求文档不存在: {candidate}")
        if candidate.suffix.lower() not in {".html", ".htm", ".md", ".markdown"}:
            raise HTTPException(status_code=400, detail="requirement_html_path 必须指向 .html/.htm/.md 文件")
        requirement = str(candidate)

    project = Path(req.project_path).expanduser()
    if not project.is_absolute():
        raise HTTPException(status_code=400, detail="project_path 必须是绝对路径")
    project = project.resolve()
    if not project.is_dir():
        raise HTTPException(status_code=400, detail=f"项目路径不存在或不是目录: {project}")
    project = _resolve_local_dev_repository(project)
    return requirement, str(project)


@app.post("/api/analyze/qoder", response_model=AnalyzeResponse)
async def analyze_qoder(req: QoderAnalyzeRequest) -> AnalyzeResponse:
    """使用 Qoder + requirement-impact-analysis skill 分析原型图对应的代码变更影响范围。"""
    resolved = _resolve_prototype_image(req.prototype_image)
    try:
        requirement, project = _validate_qoder_request(req)
        config = QoderAnalysisConfig(
            project_path=project,
            prototype_image=resolved.path,
            requirement_html_path=requirement,
            skill_name=req.skill_name,
            extra_context=req.extra_context,
            model=req.model or DEFAULT_QODER_MODEL,
            refresh_cache=req.refresh_cache,
        )
        report = await run_qoder_analysis(config)
        return AnalyzeResponse(success=True, report=report)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return AnalyzeResponse(success=False, error=str(exc))
    finally:
        resolved.cleanup()


@app.get("/api/analyze/qoder/stream")
async def analyze_qoder_stream(
    project_path: str,
    prototype_image: str | None = None,
    requirement_html_path: str | None = None,
    skill_name: str = DEFAULT_QODER_SKILL_NAME,
    extra_context: str = "",
    model: str | None = None,
    refresh_cache: bool = False,
):
    """以 SSE 推送 Qoder 分析过程文本及最终文件变更清单。"""
    req = QoderAnalyzeRequest(
        prototype_image=prototype_image,
        project_path=project_path,
        requirement_html_path=requirement_html_path,
        skill_name=skill_name,
        extra_context=extra_context,
        model=model,
        refresh_cache=refresh_cache,
    )
    requirement, project = _validate_qoder_request(req)
    resolved = _resolve_prototype_image(prototype_image)
    config = QoderAnalysisConfig(
        project_path=project,
        prototype_image=resolved.path,
        requirement_html_path=requirement,
        skill_name=skill_name,
        extra_context=extra_context,
        model=model or DEFAULT_QODER_MODEL,
        refresh_cache=refresh_cache,
    )

    async def event_stream():
        try:
            async for event in stream_qoder_analysis(config):
                data = {"type": event.event_type, "content": event.content, "metadata": event.metadata}
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            resolved.cleanup()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
