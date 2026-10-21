"""统一 HTTP 客户端（providers/http.py）

全局单例，连接池复用（性能红线手段）。
"""

import httpx

_client: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    """获取全局 httpx 客户端（单例）"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            # 服务间调用必须绕过桌面环境的 HTTP(S)_PROXY；否则 localhost:8080
            # 会被代理转发并以 502 失败，导致本地向量服务看似不可用。
            trust_env=False,
            # 超时分离：连接 5s，读 180s（流式长回答不被掐断），写 30s
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=10.0),
            limits=httpx.Limits(max_connections=50),
        )
    return _client


async def close_http() -> None:
    """关闭全局客户端"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
