"""抓取各分类下真实的 B 站数学视频，写入 app/services/bilibili_fallback.json 作为兜底数据。

资源中心在线优先走实时检索；本文件只在远端不可用（超时 / 风控 412）时兜底，
保证页面永远有封面和链接可点。分类调整后重跑本脚本刷新即可：

    uv run python scripts/harvest_bilibili.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import bilibili  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "bilibili_fallback.json"
PER_TOPIC = 12


async def fetch_with_retry(topic: dict, attempts: int = 5) -> list[dict]:
    """B 站对连续检索会限流（code=0 但 result 为空），必须退避重试。"""
    keyword = topic["keyword"]
    up_mid = topic.get("up_mid")
    for i in range(attempts):
        result = await bilibili.fetch_videos(keyword, page=1, page_size=PER_TOPIC, up_mid=up_mid)
        # 只认实时抓到的数据；fallback 是上一轮留下的，不能当成这次成功
        if result["items"] and result["source"] in ("bilibili", "cache"):
            return result["items"]
        wait = 3.0 * (i + 1)
        print(f"      重试 {i + 1}/{attempts}（{result['source']}），等 {wait:.0f}s")
        await asyncio.sleep(wait)
    return []


async def main() -> None:
    # 与已有文件合并：本轮失败（限流）的分类保留上一轮数据，避免越刷越少
    payload: dict[str, list[dict]] = {}
    if OUT.exists():
        payload = json.loads(OUT.read_text(encoding="utf-8"))

    empty: list[str] = []
    for topic in bilibili.TOPICS:
        keyword = topic["keyword"]
        items = await fetch_with_retry(topic)
        if items:
            payload[keyword] = items
        else:
            empty.append(topic["key"])
        src = f"UP {topic['up_name']}" if topic.get("up_mid") else "关键词检索"
        print(f"[{topic['key']:>10}] {keyword}（{src}）  ->  {len(items)} 条")
        for it in items[:3]:
            print(f"      {it['bvid']}  {it['title'][:34]}  @{it['author']}")
        await asyncio.sleep(3.0)  # 给 B 站限流留余量

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(len(v) for v in payload.values())
    print(f"\n写入 {OUT}：{len(payload)} 个分类 / {total} 条视频")
    if empty:
        print(f"以下分类本次未取到数据（重跑可补齐）：{empty}")


if __name__ == "__main__":
    asyncio.run(main())
