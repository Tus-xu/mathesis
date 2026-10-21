"""MinIO + 拍照题全链路验证（任务9）：上传 → MinIO 直传 → complete → parse → LaTeX 内容。

链路：
1. 生成数学题 PNG（resvg）→ POST /api/files/upload（MinIO 预签名）
2. PUT 预签名 URL（MinIO 直传）→ POST /api/files/{id}/complete
3. POST /api/files/{id}/parse（purpose=question_photo：RapidOCR 双轨 → spark_vl 云轨 LaTeX）
4. 轮询 GET /api/files/{id}，断言 status=parsed 且 content_text 含数学符号（LaTeX）
5. 清理验收数据

用法：cd services/api && .venv\\Scripts\\python.exe -m scripts.verify_photo_chain
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
PNG_PATH = Path("D:/math-arena/.tmp/photo-question.png")
MATH_HINTS = ("f", "x", "=", "²", "'", "(", ")")


async def main() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    data = PNG_PATH.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    # 验收用户 + token
    from app.gateway.jwt import create_token_with_role
    from app.models.user import User

    async with factory() as db:
        user = User(phone=f"136{uuid.uuid4().int % 100000000:08d}", nickname="拍照验收")
        db.add(user)
        await db.flush()
        uid = str(user.id)
        await db.commit()
        token = create_token_with_role(user_id=uid, role="student", roles=["student"], verified=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            # ① upload
            r = await client.post(
                f"{BASE}/api/files/upload",
                json={
                    "filename": "photo-question.png",
                    "mime": "image/png",
                    "size_bytes": len(data),
                    "sha256": sha,
                    "multipart": False,
                },
                headers=headers,
            )
            r.raise_for_status()
            up = r.json()["data"]
            file_id = up["file_id"]
            print(f"① upload: file_id={file_id} deduplicated={up['deduplicated']}")

            # ② MinIO 直传（预签名 PUT）
            put = await client.put(
                up["upload_url"],
                content=data,
                headers={"Content-Type": "image/png"},
            )
            print(f"② MinIO PUT: HTTP {put.status_code}")
            if put.status_code not in (200, 204):
                print("   MinIO 直传失败：", put.text[:200])
                return 1

            # ③ complete
            rc = await client.post(
                f"{BASE}/api/files/{file_id}/complete",
                json={"upload_id": None, "parts": []},
                headers=headers,
            )
            print(f"③ complete: {rc.json()['code']}")

            # ④ parse（拍照题）
            rp = await client.post(
                f"{BASE}/api/files/{file_id}/parse",
                json={"engine_hint": "auto", "purpose": "question_photo"},
                headers=headers,
            )
            print(f"④ parse 请求: HTTP {rp.status_code} {rp.json()['code']}")

            # ⑤ 轮询结果（内容在 data.assets[].content，ADR-007：markdown/text 带 content）
            detail = None
            for _ in range(60):
                await asyncio.sleep(3)
                rd = await client.get(f"{BASE}/api/files/{file_id}", headers=headers)
                detail = rd.json()["data"]
                if detail.get("status") in ("parsed", "failed"):
                    break
            print(f"⑤ 状态: {detail.get('status')} 引擎: {detail.get('parse_engine')}")
            content = "\n".join(
                (a.get("content") or "") for a in (detail.get("assets") or [])
            ).strip()
            print(f"   content({len(content)} 字): {content[:200]!r}")

            ok = detail.get("status") == "parsed" and any(h in content for h in ("f", "x", "="))
            print(f"\n拍照题全链路: {'✅ PASS' if ok else '❌ FAIL'}")
            if not ok:
                print(f"   error: {detail.get('error')}")
            return 0 if ok else 1
        finally:
            async with factory() as db:
                for stmt in (
                    "DELETE FROM file_assets WHERE file_id IN (SELECT id FROM files WHERE user_id=:u)",
                    "DELETE FROM files WHERE user_id=:u",
                    "DELETE FROM users WHERE id=:u",
                ):
                    await db.execute(text(stmt), {"u": uuid.UUID(uid)})
                await db.commit()
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
