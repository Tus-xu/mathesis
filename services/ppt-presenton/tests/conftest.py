"""8201 测试 conftest：注入 8200 (VPL) 路径以便跨服务测试。"""
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[2] / "ppt-studio-v2"
if str(STUDIO) not in sys.path:
    sys.path.insert(0, str(STUDIO))