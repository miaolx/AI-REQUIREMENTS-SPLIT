"""
需求影响分析服务 — FastAPI 应用

暴露 REST 接口，接收产品文档 URL + 原型图地址，
调用 Qoder Agent SDK + requirement-impact-analysis skill
对指定项目代码进行影响范围分析。

启动方式:
    python app.py

环境变量:
    QODER_PERSONAL_ACCESS_TOKEN  — Qoder PAT（可选，未设置时自动使用本地 qodercli 登录态）
"""

import ast
import json
import os
import re
import tempfile
import urllib.request
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from analyzer import (
    AnalysisConfig,
    run_analysis,
    stream_analysis,
)

# ── Pydantic 模型 ─────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    """分析请求体。"""

    prd_url: str = Field(..., description="产品文档 / 需求地址 URL")
    prototype_image: str | None = Field(default=None, description="原型图：本地绝对路径或 http/https URL（可选，URL 自动下载）")
    project_path: str = Field(..., description="待分析代码项目根目录（绝对路径）")
    skill_name: str = Field(
        default="requirement-impact-analysis",
        description="要调用的 skill 名称",
    )
    skill_path: str | None = Field(
        default=None,
        description="自定义 skill 目录路径（可选，默认使用用户级 skill）",
    )
    extra_context: str = Field(
        default="",
        description="额外项目上下文信息（技术栈/包结构/规范约束等）",
    )
    model: str | None = Field(
        default=None,
        description="模型别名(sonnet/opus/haiku)或完整模型 ID，不传则用 CLI 默认模型",
    )


class AnalyzeResponse(BaseModel):
    """分析完成响应体。"""

    success: bool
    report: str = ""
    error: str = ""


# ── JSON 容错解析（对接口透明，不影响参数传递）───────────


def _decode_json_lenient(text: str):
    """
    容错解析 JSON 文本：
    1) 标准 JSON
    2) 去除尾逗号后重试: {"a": 1,} → {"a": 1}
    3) Python 字面量兜底（单引号等）: {'a': 1} → {"a": 1}

    注意：Windows 路径反斜杠仍需在 JSON 中双写转义（\\）或改用正斜杠（/）。
    """
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
            "请求体不是合法 JSON。请检查："
            "1) 属性名和字符串必须使用双引号；"
            "2) Windows 路径反斜杠需双写转义(\\\\)或改用正斜杠(/)；"
            "3) 不要有尾逗号。"
        ),
    )


class TolerantRequest(Request):
    """请求类：重写 json() 实现容错解析，其余行为与标准 Request 一致。"""

    async def json(self):
        raw = await self.body()
        text = raw.decode("utf-8-sig").strip()
        if not text:
            raise HTTPException(status_code=400, detail="请求体为空")
        return _decode_json_lenient(text)


class TolerantRoute(APIRoute):
    """路由类：把请求对象替换为 TolerantRequest，接口签名保持 Pydantic 模型不变。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def custom_route_handler(request: Request):
            request.__class__ = TolerantRequest
            return await original(request)

        return custom_route_handler


# ── FastAPI 应用 ───────────────────────────────────────────

app = FastAPI(
    title="需求影响分析服务",
    description="基于 Qoder Agent SDK，接收产品文档和原型图，分析代码影响范围",
    version="1.0.0",
)

# 所有路由启用 JSON 容错解析（接口签名/参数不受影响）
app.router.route_class = TolerantRoute


@app.get("/health")
async def health():
    """健康检查。"""
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """
    同步分析接口：阻塞等待分析完成后返回完整报告。

    适合一次性获取结果，不适合实时展示进度。
    请求体 JSON 解析做了容错处理：支持单引号、尾逗号等常见格式问题。
    """
    _validate_request(req)

    config = AnalysisConfig(
        prd_url=req.prd_url,
        prototype_image=_resolve_prototype_image(req.prototype_image),
        project_path=req.project_path,
        skill_name=req.skill_name,
        skill_path=req.skill_path,
        extra_context=req.extra_context,
        model=req.model,
    )

    try:
        report = await run_analysis(config)
        return AnalyzeResponse(success=True, report=report)
    except Exception as e:
        return AnalyzeResponse(success=False, error=str(e))


@app.get("/api/analyze/stream")
async def analyze_stream(
    prd_url: str,
    project_path: str,
    prototype_image: str | None = None,
    skill_name: str = "requirement-impact-analysis",
    skill_path: str | None = None,
    extra_context: str = "",
    model: str | None = None,
):
    """
    流式分析接口（SSE）：实时推送分析进度。

    参数通过 query string 传递，返回 text/event-stream。
    每条事件格式: data: {json}\\n\\n
    """

    config = AnalysisConfig(
        prd_url=prd_url,
        prototype_image=_resolve_prototype_image(prototype_image),
        project_path=project_path,
        skill_name=skill_name,
        skill_path=skill_path,
        extra_context=extra_context,
        model=model,
    )

    async def event_stream():
        try:
            async for event in stream_analysis(config):
                data = {
                    "type": event.event_type,
                    "content": event.content,
                    "metadata": event.metadata,
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_data = {
                "type": "error",
                "content": f"分析失败: {e}",
                "metadata": {},
            }
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 校验 ──────────────────────────────────────────────────


def _validate_request(req: AnalyzeRequest) -> None:
    """请求参数校验。"""
    if not req.prd_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="prd_url 必须是 http/https URL")

    if not os.path.isabs(req.project_path):
        raise HTTPException(
            status_code=400,
            detail="project_path 必须是绝对路径",
        )
    if not os.path.isdir(req.project_path):
        raise HTTPException(
            status_code=400,
            detail=f"项目路径不存在或不是目录: {req.project_path}",
        )


def _resolve_prototype_image(prototype_image: str | None) -> str | None:
    """解析原型图为本地文件路径：支持 URL（自动下载到临时文件）和本地绝对路径。"""
    if not prototype_image:
        return None

    # URL：下载到临时文件
    if prototype_image.startswith(("http://", "https://")):
        suffix = os.path.splitext(urlparse(prototype_image).path)[1] or ".png"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        try:
            urllib.request.urlretrieve(prototype_image, tmp.name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"下载原型图失败: {e}")
        return tmp.name

    # 本地路径：校验绝对路径 + 存在
    if not os.path.isabs(prototype_image):
        raise HTTPException(
            status_code=400,
            detail="prototype_image 必须是绝对路径或 http/https URL",
        )
    if not os.path.exists(prototype_image):
        raise HTTPException(
            status_code=400,
            detail=f"原型图文件不存在: {prototype_image}",
        )
    return prototype_image


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "9000"))
    # 注意：Windows 下不开 reload。reload 的父子进程 socket 继承会导致
    # accept 失效（请求挂起）；SDK 子进程问题已由 analyzer 线程桥接解决。
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
