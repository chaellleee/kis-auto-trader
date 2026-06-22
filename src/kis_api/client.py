"""
KIS REST 클라이언트.

환경변수(.env)를 읽어 모의/실전 모드를 자동 선택하고,
공통 GET/POST 헬퍼와 표준 헤더를 제공한다.
"""
import os
import time

import requests
from dotenv import dotenv_values, load_dotenv

from ..utils.logger import get_logger
from .auth import DOMAINS, TokenManager

log = get_logger("kis.client")

load_dotenv()


def _is_placeholder(v: str | None) -> bool:
    """비었거나 템플릿 placeholder 면 True → 진짜 값을 덮어쓰지 않는다.

    - '여기에_...'  (앱키/시크릿 placeholder)
    - '00000000-01' 같은 0으로만 된 계좌 placeholder
    """
    if not v:
        return True
    s = v.strip()
    if "여기에" in s:
        return True
    head = s.split("-")[0]
    if head and set(head) == {"0"}:
        return True
    return False


def _load_env_for_mode(mode: str) -> str:
    """
    모드별 환경파일(env/.env.<mode>)에서 '실제 값만' 골라 환경변수에 반영한다.
    placeholder/빈 값은 무시하므로, 키를 최상위 .env 에 넣었든 env/.env.<mode> 에
    넣었든 어느 쪽이든 동작한다.
    반환: 로드한 파일 경로(없으면 빈 문자열).
    """
    path = os.getenv("KIS_ENV_FILE") or os.path.join("env", f".env.{mode}")
    if not os.path.exists(path):
        return ""
    for k, v in dotenv_values(path).items():
        if not _is_placeholder(v):
            os.environ[k] = v
    return path


class KISClient:
    """모의/실전 공통 REST 클라이언트. 환경은 KIS_MODE 로 완전히 분리된다."""

    def __init__(self, mode: str | None = None):
        self.mode = (mode or os.getenv("KIS_MODE", "paper")).lower()
        if self.mode not in DOMAINS:
            raise ValueError(f"KIS_MODE 는 paper|real 이어야 합니다. (현재: {self.mode})")

        loaded = _load_env_for_mode(self.mode)
        if not loaded:
            log.warning("[%s] 환경파일이 없습니다. env/.env.%s 를 만들어 주세요 "
                        "(env/.env.%s.example 참고).", self.mode, self.mode, self.mode)

        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        account = os.getenv("KIS_ACCOUNT", "00000000-01")

        if not self.app_key or not self.app_secret:
            log.warning("[%s] APP_KEY/SECRET 이 비어있습니다. %s 를 확인하세요.",
                        self.mode, loaded or f"env/.env.{self.mode}")

        self.cano, self.acnt_prdt_cd = (account.split("-") + ["01"])[:2]

        self.base_url = DOMAINS[self.mode]
        self.token_mgr = TokenManager(self.mode, self.app_key, self.app_secret)
        self._last_call = 0.0
        self.is_paper = self.mode == "paper"
        log.info("KISClient 초기화: mode=%s, account=%s", self.mode, account)

    def _headers(self, tr_id: str, extra: dict | None = None) -> dict:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token_mgr.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            h.update(extra)
        return h

    def _throttle(self, min_interval: float = 0.06) -> None:
        """초당 호출 제한(약 20건/초) 보호용 간단 쓰로틀."""
        dt = time.time() - self._last_call
        if dt < min_interval:
            time.sleep(min_interval - dt)
        self._last_call = time.time()

    def get(self, path: str, tr_id: str, params: dict,
            extra_headers: dict | None = None, retries: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(retries):
            self._throttle()
            try:
                res = requests.get(url, headers=self._headers(tr_id, extra_headers),
                                   params=params, timeout=10)
                if res.status_code == 200:
                    return res.json()
                log.warning("GET %s 실패 %s: %s", path, res.status_code, res.text[:200])
            except requests.RequestException as e:
                log.warning("GET %s 예외(%d/%d): %s", path, attempt + 1, retries, e)
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"GET {path} 재시도 초과")

    def post(self, path: str, tr_id: str, body: dict,
             use_hashkey: bool = True, retries: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        extra = {}
        if use_hashkey:
            extra["hashkey"] = self.token_mgr.hashkey(body)
        for attempt in range(retries):
            self._throttle()
            try:
                res = requests.post(url, headers=self._headers(tr_id, extra),
                                    json=body, timeout=10)
                if res.status_code == 200:
                    return res.json()
                log.warning("POST %s 실패 %s: %s", path, res.status_code, res.text[:200])
            except requests.RequestException as e:
                log.warning("POST %s 예외(%d/%d): %s", path, attempt + 1, retries, e)
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"POST {path} 재시도 초과")
