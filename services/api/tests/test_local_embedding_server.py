from fastapi.testclient import TestClient
from pathlib import Path


def test_local_embedding_server_keeps_existing_openai_embedding_contract():
    """若本地服务改变响应格式，现有 EmbeddingProvider 会再次把教材导入阻断。"""
    from scripts.local_embedding_server import create_app

    app = create_app(lambda texts: [[0.25] * 1024 for _ in texts])
    response = TestClient(app).post(
        "/v1/embeddings",
        json={"model": "bge-m3", "input": ["集合的概念", "函数的定义域"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["model"] == "bge-m3"
    assert [row["index"] for row in body["data"]] == [0, 1]
    assert all(len(row["embedding"]) == 1024 for row in body["data"])


def test_embedding_health_refuses_incomplete_pytorch_weights(tmp_path: Path):
    """损坏模型绝不能被 /health 伪报为可用，避免教材被零向量或半截权重污染。"""
    from scripts.local_embedding_server import create_app

    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    (model_dir / "pytorch_model.bin").write_bytes(b"truncated-weight")
    app = create_app(model_path=model_dir)

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "BGE-M3 权重不完整或无法加载"
