"""tests/ 根级 conftest：marker 注册（pyproject 归 B3，B5 用此文件声明自有 marker）。"""


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "compose: 需要 docker compose 全栈（DB+Valkey+MinIO）真实容器的用例（红线 5 禁进程内模拟）；"
        "跑法：TEACHER_CONTRACT_BASE_URL=http://127.0.0.1:8100 pytest -m compose",
    )
