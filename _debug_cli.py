"""临时调试脚本：复现 SDK 启动 CLI 的真实异常堆栈。"""

import asyncio
import traceback

from qoder_agent_sdk import query

from analyzer import AnalysisConfig, _build_options, _build_prompt


async def main() -> None:
    print(">>> start", flush=True)
    config = AnalysisConfig(
        prd_url="https://example.com/prd",
        project_path=r"c:\Users\miaolx\Desktop\AI-CodeReview\AI-requirements-split",
    )
    options = _build_options(
        config,
        stderr_callback=lambda line: print(f"[stderr] {line}", flush=True),
    )
    try:
        async for message in query(prompt=_build_prompt(config), options=options):
            print("MSG:", type(message).__name__, flush=True)
            break
        print(">>> done", flush=True)
    except BaseException:
        print(">>> exception", flush=True)
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
