"""多模态输入链路实机测试（迭代06）

真实走 上传→存储→解析→状态轮询 全链路：
- md 文本（direct 引擎：验证存储+管线）
- PDF（pymupdf 引擎：用 PyMuPDF 现场生成带文字的 PDF）
- 图片（rapidocr 引擎：用 PIL 现场绘制含公式文字的 PNG）

用法：先起 API（uvicorn app.main:app --port 8000），再运行
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/multimodal_probe.py
"""
import hashlib
import io
import json
import time

import httpx

BASE = "http://127.0.0.1:8000"
PHONE = "13900139009"

c = httpx.Client(timeout=60)


def make_pdf() -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Math test: solve x^2 - 5x + 6 = 0", fontsize=14)
    page.insert_text((72, 130), "Hint: factorization (x-2)(x-3)=0", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def make_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 160), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 40), "Solve equation x2 - 5x + 6 = 0", fill="black")
    d.text((20, 90), "Answer: x = 2 or x = 3", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload_and_parse(headers: dict, filename: str, mime: str, data: bytes, purpose: str) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    r = c.post(f"{BASE}/api/files/upload", headers=headers, json={
        "filename": filename, "mime": mime, "size_bytes": len(data), "sha256": sha,
    })
    d = r.json()
    if d.get("code") != 0:
        return {"step": "upload", "error": d}
    file_id = d["data"]["file_id"]
    upload_url = d["data"]["upload_url"]

    # 直传预签名 URL（单分片）
    r = c.put(upload_url, content=data, headers={"Content-Type": mime})
    if r.status_code not in (200, 201):
        return {"step": "put", "error": f"HTTP {r.status_code}: {r.text[:200]}"}

    r = c.post(f"{BASE}/api/files/{file_id}/parse", headers=headers,
               json={"engine_hint": "auto", "purpose": purpose})
    d = r.json()
    if d.get("code") != 0:
        return {"step": "parse", "error": d}

    # 轮询解析状态（后台任务）
    for _ in range(30):
        time.sleep(1)
        d = c.get(f"{BASE}/api/files/{file_id}", headers=headers).json()
        info = d.get("data", {})
        if info.get("status") in ("parsed", "failed"):
            return {"step": "done", "file_id": file_id, "status": info.get("status"),
                    "engine": info.get("parse_engine"), "error": info.get("error")}
    return {"step": "poll", "error": "timeout 30s"}


def main():
    r = c.post(f"{BASE}/api/auth/sms-code", json={"phone": PHONE})
    if r.json().get("code") != 0:
        print("[x] sms 受限，60 秒后重跑:", r.json().get("message"))
        raise SystemExit(1)
    tok = c.post(f"{BASE}/api/auth/login", json={"phone": PHONE, "code": "123456"}).json()["data"]["token"]
    h = {"Authorization": f"Bearer {tok}"}
    print("[1] 登录 OK")

    cases = [
        ("笔记.md", "text/markdown", "# 函数单调性\n\n定义：任取 x1 < x2，若 f(x1) < f(x2) 则单调递增。".encode(), "chat_attach"),
        ("试卷.pdf", "application/pdf", make_pdf(), "question_photo"),
        ("题目.png", "image/png", make_png(), "question_photo"),
    ]
    for filename, mime, data, purpose in cases:
        print(f"\n[上传] {filename} ({mime}, {len(data)}B)")
        res = upload_and_parse(h, filename, mime, data, purpose)
        print("   ", json.dumps(res, ensure_ascii=False)[:300])

        # 若解析成功，验证内容真的被抽出来了
        if res.get("status") == "parsed":
            fid = res["file_id"]
            # 通过 assets 不行（需 asset_id），直接再触发 parse 是幂等的；改查文件详情里的 assets
            d = c.get(f"{BASE}/api/files/{fid}", headers=h).json().get("data", {})
            assets = d.get("assets") or []
            for a in assets[:1]:
                content = (a.get("content") or "")[:200]
                print(f"    内容摘录: {content}")


if __name__ == "__main__":
    main()
