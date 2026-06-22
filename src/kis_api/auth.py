"""
KIS OAuth 접근토큰 발급 및 캐싱.

KIS 접근토큰은 발급 후 약 24시간 유효하며, 너무 자주 재발급하면
'초당 호출 제한'에 걸립니다. 따라서 디스크에 캐싱했다가 만료 전이면 재사용합니다.
"""
import json
import os
import time
from datetime import datetime, timedelta

import requests

from ..utils.logger import get_logger

log = get_logger("kis.auth")

DOMAINS = {
    "paper": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}

_TOKEN_CACHE = ".token_cache.json"


class TokenManager:
    """접근토큰을 발급/캐싱/갱신한다."""

    def __init__(self, mode: str, app_key: str, app_secret: str):
        if mode not in DOMAINS:
            raise ValueError(f"mode 는 'paper' 또는 'real' 이어야 합니다. (입력: {mode})")
        self.mode = mode
        self.base_url = DOMAINS[mode]
        self.app_key = app_key
        self.app_secret = app_secret
        self._token: str | None = None
        self._expire_at: float = 0.0

    def get_token(self) -> str:
        """유효한 접근토큰을 반환. 캐시 → 메모리 → 신규발급 순서."""
        now = time.time()
        if self._token and now < self._expire_at - 60:
            return self._token

        cached = self._load_cache()
        if cached and now < cached["expire_at"] - 60:
            self._token = cached["token"]
            self._expire_at = cached["expire_at"]
            return self._token

        return self._issue()

    def _issue(self) -> str:
        """신규 토큰 발급 (POST /oauth2/tokenP)."""
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            try:
                detail = res.json()
            except Exception:
                detail = res.text[:300]
            hint = ("앱키/앱시크릿이 올바른지, '모의투자' 전용 키를 모의 도메인(:29443)에 "
                    "쓰고 있는지, KIS Developers 에서 앱이 승인됐는지 확인하세요.")
            raise RuntimeError(
                f"토큰 발급 실패 (HTTP {res.status_code}) [{self.mode}]: {detail}\n→ {hint}"
            )
        data = res.json()

        if "access_token" not in data:
            raise RuntimeError(f"토큰 발급 실패: {data}")

        self._token = data["access_token"]
        ttl = int(data.get("expires_in", 23 * 3600))
        self._expire_at = time.time() + ttl
        self._save_cache()
        log.info("[%s] 접근토큰 신규 발급 (만료: %s)",
                 self.mode, datetime.fromtimestamp(self._expire_at))
        return self._token

    def hashkey(self, body: dict) -> str:
        """주문 등 POST 요청 시 필요한 hashkey 생성."""
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        res.raise_for_status()
        return res.json()["HASH"]

    def _cache_key(self) -> str:
        return f"{self.mode}:{self.app_key[:8]}"

    def _load_cache(self) -> dict | None:
        if not os.path.exists(_TOKEN_CACHE):
            return None
        try:
            with open(_TOKEN_CACHE, "r", encoding="utf-8") as f:
                all_cache = json.load(f)
            return all_cache.get(self._cache_key())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_cache(self) -> None:
        all_cache = {}
        if os.path.exists(_TOKEN_CACHE):
            try:
                with open(_TOKEN_CACHE, "r", encoding="utf-8") as f:
                    all_cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                all_cache = {}
        all_cache[self._cache_key()] = {
            "token": self._token,
            "expire_at": self._expire_at,
        }
        try:
            with open(_TOKEN_CACHE, "w", encoding="utf-8") as f:
                json.dump(all_cache, f)
        except OSError:
            log.warning("토큰 캐시 저장 실패 (무시하고 진행)")
