"""Worker 入口（8200）：委托 app/worker.py（保持 `python worker.py` 可用）。"""
from app.worker import entry

if __name__ == "__main__":
    entry()
