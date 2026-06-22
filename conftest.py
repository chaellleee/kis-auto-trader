"""
pytest 루트 설정.

레포 루트를 sys.path 에 추가해 `from src...` / `from tests...` 임포트가
Cursor 테스트 러너·pytest 어디서 실행하든 동작하게 한다(별도 설치 불필요).
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
