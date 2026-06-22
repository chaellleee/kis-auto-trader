"""
퀄리티(Quality) 펀더멘털 팩터 패널.

기술적/가격 팩터만 쓰면 시장이 '가치/우량주' 국면으로 바뀔 때 소외된다.
재무 우량성을 결합하면 한국처럼 사이클이 강한 시장에서 모멘텀의 승률이 올라간다.

데이터
------
- pykrx `get_market_fundamental(date)` 는 그 시점의 BPS/PER/PBR/EPS/DIV/DPS 스냅샷을 준다.
- 이로부터 두 가지 퀄리티 지표를 만든다 (정식 재무제표가 없어도 사용 가능한 프록시):
    * ROE 프록시   = EPS / BPS            (자기자본이익률 근사)
    * 이익수익률   = 1 / PER  (= EPS/Price)  (저PER·고이익 우량성)
- 월말 스냅샷을 모아 (date × code) 패널로 캐싱하고, 일자 조회 시 '직전 가용 스냅샷'을
  사용해 룩어헤드(미래참조)를 피한다.

OPM·CAPEX/현금흐름 같은 더 정교한 퀄리티를 쓰고 싶다면, 같은 형식의
(date, code, quality_score) CSV 를 data/cache/quality_custom.csv 로 넣으면 우선 사용된다.

펀더멘털이 전혀 없으면(오프라인/합성데이터) 패널이 비어 있고,
전략은 자동으로 퀄리티 가중치를 0 으로 두고 나머지 팩터로만 동작한다.
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd

from ..utils.cacheio import load_df, save_df
from ..utils.logger import get_logger

log = get_logger("data.fundamentals")

CACHE_DIR = os.path.join("data", "cache")
QUALITY_CACHE = os.path.join(CACHE_DIR, "quality")
QUALITY_CUSTOM = os.path.join(CACHE_DIR, "quality_custom.csv")


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-3, 3)


def fetch_quality_panel(start: str, end: str | None = None,
                        codes: list[str] | None = None) -> pd.DataFrame:
    """
    월말 스냅샷으로 퀄리티 종합 z-score 패널 생성/캐싱.
    반환: index=date(월말), columns=code, value=quality z-score
    """
    from pykrx import stock

    end = end or datetime.now().strftime("%Y%m%d")
    os.makedirs(CACHE_DIR, exist_ok=True)

    month_ends = pd.date_range(pd.to_datetime(start), pd.to_datetime(end), freq="ME")
    rows = []
    from tqdm import tqdm
    for d in tqdm(month_ends, desc="퀄리티 스냅샷"):
        ds = d.strftime("%Y%m%d")
        try:
            f = stock.get_market_fundamental(ds, market="ALL")
        except Exception as e:
            log.debug("%s 펀더멘털 실패: %s", ds, e)
            continue
        if f is None or f.empty:
            continue
        f = f.replace(0, np.nan)
        roe = (f["EPS"] / f["BPS"]).replace([np.inf, -np.inf], np.nan)
        earnings_yield = (1.0 / f["PER"]).replace([np.inf, -np.inf], np.nan)
        quality = _zscore(roe).fillna(0) + _zscore(earnings_yield).fillna(0)
        snap = pd.DataFrame({"date": d, "code": quality.index, "quality": quality.values})
        if codes is not None:
            snap = snap[snap["code"].isin(codes)]
        rows.append(snap)

    if not rows:
        log.warning("퀄리티 스냅샷이 비었습니다(네트워크/pykrx 확인). 퀄리티 팩터는 비활성화됩니다.")
        return pd.DataFrame()

    long = pd.concat(rows, ignore_index=True)
    panel = long.pivot(index="date", columns="code", values="quality").sort_index()
    save_df(panel.reset_index(), QUALITY_CACHE)
    log.info("퀄리티 패널 저장: %d개월 x %d종목", *panel.shape)
    return panel


def load_quality_panel() -> pd.DataFrame | None:
    """캐시된 퀄리티 패널 로드. 커스텀 CSV 가 있으면 우선. 없으면 None."""
    if os.path.exists(QUALITY_CUSTOM):
        df = pd.read_csv(QUALITY_CUSTOM, dtype={"code": str})
        df["date"] = pd.to_datetime(df["date"])
        panel = df.pivot(index="date", columns="code", values="quality").sort_index()
        log.info("커스텀 퀄리티 패널 사용: %d x %d", *panel.shape)
        return panel
    df = load_df(QUALITY_CACHE)
    if df is not None and not df.empty:
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    return None


class QualityProvider:
    """as_of 시점에 사용 가능한(직전) 퀄리티 스냅샷을 종목별로 제공."""

    def __init__(self, panel: pd.DataFrame | None):
        self.panel = panel
        self.enabled = panel is not None and not panel.empty
        if self.panel is not None and self.enabled:
            self.panel = self.panel.sort_index()

    def get(self, as_of: pd.Timestamp) -> pd.Series:
        """as_of 이하 가장 최근 스냅샷. 없으면 빈 Series."""
        if not self.enabled:
            return pd.Series(dtype=float)
        avail = self.panel.loc[:as_of]
        if avail.empty:
            return pd.Series(dtype=float)
        return avail.iloc[-1].dropna()
