# -*- coding: utf-8 -*-
"""Spark-X2.5-4B（讯飞 MaaS）主通道接入运行时验证（一次性取证脚本）

验证点：
1. SparkProvider 从 .env 读到新端点/模型/密钥并 available
2. 非流式 chat（中文，thinking 默认 disabled）
3. 流式 chat_stream（token 事件 + 流末 usage + finish）
4. ModelRouter 意向通道 = spark（主通道恢复启用）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.providers.spark import SparkProvider  # noqa: E402


async def main() -> None:
    p = SparkProvider()
    print("== 1. 配置装载 ==")
    print("spark_model:", settings.spark_model)
    print("spark_base_url:", settings.spark_base_url)
    print("provider._api_url:", p._api_url)
    print("provider.available:", p.available)
    assert p.available, "SPARK_API_PASSWORD 未配置"

    print("\n== 2. 非流式 chat（中文） ==")
    r = await p.chat(
        [{"role": "user", "content": "用一句话说明勾股定理。"}],
        max_tokens=500,
        request_id="verify-spark-maas",
        scene="verify_spark",
    )
    print("provider:", r["provider"], "| model:", r["model"], "| latency_ms:", r["latency_ms"])
    print("in/out tokens:", r["input_tokens"], "/", r["output_tokens"])
    print("content:", r["content"][:200])
    assert r["provider"] == "spark" and r["model"] == "spark-x2.5-4b"
    assert r["content"].strip(), "非流式返回空内容"

    print("\n== 3. 流式 chat_stream ==")
    tokens: list[str] = []
    usage: dict = {}
    finish = None
    async for ev in p.chat_stream(
        [{"role": "user", "content": "解方程 x^2-5x+6=0，先给答案再一句话过程。"}],
        max_tokens=2000,
        request_id="verify-spark-maas-stream",
        scene="verify_spark",
    ):
        if "token" in ev:
            tokens.append(ev["token"])
        elif "_usage" in ev:
            usage = ev["_usage"] or {}
        elif "_finish" in ev:
            finish = ev["_finish"]
    text = "".join(tokens)
    print("stream chars:", len(text), "| finish:", finish)
    print("usage:", usage)
    print("stream text head:", text[:150])
    assert text.strip(), "流式未输出 token"
    assert finish == "stop", f"finish 异常: {finish}"

    print("\n== 4. Router 通道选择 ==")
    from app.providers.router import get_model_router

    print("intended_provider:", get_model_router().intended_provider)
    assert get_model_router().intended_provider == "spark"

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
