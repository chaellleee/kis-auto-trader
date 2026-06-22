"""
한국 시장 멀티팩터 패널 (Value / Quality / Size / 수급).

배경(실증)
----------
한국 시장에서 비교적 강건하게 재현되는 프리미엄은 **가치(Value)·저변동성·사이즈**,
그리고 한국 특유의 **수급(외국인·기관 순매수)** 이다. 반대로 단순 가격 모멘텀과
수익성(Quality) 단독은 약하다. 그래서 이 모듈은 Value/Size/수급을 핵심으로,
Quality 는 보조로 제공한다.

각 팩터는 '월말 스냅샷'을 모아 (date × code) 패널로 캐싱한다. 조회 시 as_of 직전
스냅샷만 사용해 룩어헤드(미래참조)를 막는다. 저변동성은 가격(close)에서 전략이
직접 계산하므로 여기서 만들지 않는다.

데이터 소스: pykrx (KRX 공개데이터)
  - get_market_fundamental : PER/PBR/EPS/BPS/DIV  → Value, Quality
  - get_market_cap         : 시가총액            → Size
  - get_market_net_purchases_of_equities : 투자자별 순매수 → 수급

오프라인/미설치/호출실패 시 해당 패널은 비고 자동 비활성된다(전략이 가용 팩터로만 동작).
정밀 재무(OPM·부채비율·FCF)는 data/cache/factor_<name>_custom.csv 로 직접 주입 가능.
"""
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..utils.cacheio import load_df, save_df
from ..utils.logger import get_logger

log = get_logger("data.factors")

CACHE_DIR = os.path.join("data", "cache")
FACTORS = ("value", "quality", "size", "supply_demand")


def _cache_base(name: str) -> str:
    return os.path.join(CACHE_DIR, f"factor_{name}")


def _custom_csv(name: str) -> str:
    return os.path.join(CACHE_DIR, f"factor_{name}_custom.csv")


def _zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-3, 3)


def fetch_factor_panels(start: str, end: str | None = None,
                        codes: list[str] | None = None,
                        use_pykrx: bool = False) -> dict:
    """
    팩터 패널 생성·캐싱. 반환: {factor_name: DataFrame(date × code)}.

    데이터 소스 현실
    ----------------
    - size  : **FDR 발행주식수 × 가격캐시** 로 생성 (pykrx 불필요, 항상 시도).
              한국에서 가장 강한 프리미엄(소형주)이라 우선순위가 높다.
    - value/quality/supply_demand : pykrx 의존인데, 최신 pykrx 는 KRX 로그인(KRX_ID/PW)을
              요구해 대부분 실패한다. 기본은 비활성(use_pykrx=False).
              정식 펀더멘털은 DART 연동(build_value_quality_from_dart)으로 대체 권장.
    """
    panels = {}

    size = _build_size_panel(start, end, codes)
    if size is not None and not size.empty:
        save_df(size.reset_index(), _cache_base("size"))
        log.info("팩터 'size' 패널 저장: %d개월 x %d종목 (FDR 발행주식수 기반)", *size.shape)
        panels["size"] = size
    else:
        log.warning("size 패널 생성 실패(FDR 발행주식수 조회 불가). 저변동성 단독으로 동작.")
        panels["size"] = pd.DataFrame()

    if use_pykrx:
        panels.update(_fetch_pykrx_panels(start, end, codes))
    else:
        log.warning("value/quality/supply_demand 는 비활성 상태입니다. "
                    "(최신 pykrx 가 KRX 인증을 요구함 → DART 연동 권장)")
        for f in ("value", "quality", "supply_demand"):
            panels[f] = pd.DataFrame()

    return panels


def fetch_shares_fdr() -> pd.Series | None:
    """FinanceDataReader 상장목록에서 종목별 발행주식수를 가져온다(현재 스냅샷)."""
    try:
        import FinanceDataReader as fdr
    except Exception:
        return None
    for market in ("KRX", "KOSPI"):
        try:
            lst = fdr.StockListing(market)
        except Exception:
            continue
        if lst is None or lst.empty:
            continue
        code_col = next((c for c in ("Code", "Symbol") if c in lst.columns), None)
        sh_col = next((c for c in ("Stocks", "상장주식수", "Shares") if c in lst.columns), None)
        if code_col and sh_col:
            s = pd.Series(pd.to_numeric(lst[sh_col], errors="coerce").values,
                          index=lst[code_col].astype(str).str.zfill(6))
            s = s[s > 0].dropna()
            if not s.empty:
                return s
    return None


def _build_size_panel(start, end, codes) -> pd.DataFrame | None:
    """
    월말 시가총액(= 종가 × 발행주식수)으로 사이즈 점수(-log 시총) 패널 생성.
    발행주식수는 FDR 현재값을 사용(천천히 변하므로 사이즈 랭킹엔 충분).
    가격은 이미 받아둔 캐시를 사용하므로 추가 네트워크 호출이 거의 없다.
    """
    from . import universe
    shares = fetch_shares_fdr()
    if shares is None or shares.empty:
        return None
    try:
        close = universe.load_price_panel()
    except Exception:
        return None
    close = close.loc[str(start):]
    if end:
        close = close.loc[:str(pd.to_datetime(end).date())] if "-" in str(end) \
            else close.loc[:f"{end[:4]}-{end[4:6]}-{end[6:]}"]
    me = close.resample("ME").last()
    common = [c for c in me.columns if c in shares.index]
    if codes is not None:
        common = [c for c in common if c in set(codes)]
    if not common:
        return None
    mcap = me[common].mul(shares.reindex(common), axis=1)
    score = -np.log(mcap.where(mcap > 0))
    z = score.sub(score.mean(axis=1), axis=0).div(score.std(axis=1, ddof=0), axis=0)
    return z.clip(-3, 3).dropna(how="all")


def _fetch_pykrx_panels(start, end, codes) -> dict:
    """pykrx 기반 value/quality/supply_demand (KRX 인증 필요 — 실패 시 빈 DF)."""
    end = end or datetime.now().strftime("%Y%m%d")
    month_ends = pd.date_range(pd.to_datetime(start), pd.to_datetime(end), freq="ME")
    rows = {f: [] for f in ("value", "quality", "supply_demand")}
    from tqdm import tqdm
    for d in tqdm(month_ends, desc="pykrx 팩터(월)"):
        ds = d.strftime("%Y%m%d")
        val, qual = _value_quality_snapshot(ds)
        sd = _supply_demand_snapshot(d)
        for name, snap in (("value", val), ("quality", qual), ("supply_demand", sd)):
            if snap is None or snap.empty:
                continue
            df = snap.rename("score").reset_index()
            df.columns = ["code", "score"]
            df["date"] = d
            if codes is not None:
                df = df[df["code"].isin(codes)]
            rows[name].append(df)
    panels = {}
    for name in ("value", "quality", "supply_demand"):
        if not rows[name]:
            panels[name] = pd.DataFrame()
            continue
        long = pd.concat(rows[name], ignore_index=True)
        panel = long.pivot(index="date", columns="code", values="score").sort_index()
        save_df(panel.reset_index(), _cache_base(name))
        log.info("팩터 '%s' 패널 저장: %d개월 x %d종목", name, *panel.shape)
        panels[name] = panel
    return panels


def _value_quality_snapshot(ds: str):
    """get_market_fundamental → (value, quality) cross-section z-score."""
    try:
        from pykrx import stock
        f = stock.get_market_fundamental(ds, market="ALL")
    except Exception:
        return None, None
    if f is None or f.empty:
        return None, None
    f = f.replace(0, np.nan)
    earnings_yield = (1.0 / f["PER"]).replace([np.inf, -np.inf], np.nan)
    book_to_market = (1.0 / f["PBR"]).replace([np.inf, -np.inf], np.nan)
    div_yield = f.get("DIV")
    value = _zscore(earnings_yield).fillna(0) + _zscore(book_to_market).fillna(0)
    if div_yield is not None:
        value = value + _zscore(div_yield).fillna(0)
    roe = (f["EPS"] / f["BPS"]).replace([np.inf, -np.inf], np.nan)
    quality = _zscore(roe)
    return value, quality


def _size_snapshot(ds: str):
    """get_market_cap → 사이즈(소형 우위) z-score."""
    try:
        from pykrx import stock
        cap = stock.get_market_cap(ds, market="ALL")
    except Exception:
        return None
    if cap is None or cap.empty:
        return None
    col = next((c for c in ("시가총액", "Marcap") if c in cap.columns), None)
    if col is None:
        return None
    mc = pd.to_numeric(cap[col], errors="coerce").replace(0, np.nan)
    return _zscore(-np.log(mc))


def _supply_demand_snapshot(d: pd.Timestamp, lookback_days: int = 40):
    """
    최근 lookback 구간 외국인+기관 순매수액 / 시가총액 → 수급 z-score.
    get_market_net_purchases_of_equities 시그니처 차이를 대비해 보수적으로 처리.
    """
    try:
        from pykrx import stock
    except Exception:
        return None
    frm = (d - timedelta(days=lookback_days)).strftime("%Y%m%d")
    to = d.strftime("%Y%m%d")

    def _net(market, investor):
        try:
            df = stock.get_market_net_purchases_of_equities(frm, to, market, investor)
        except Exception:
            return None
        if df is None or df.empty:
            return None
        col = next((c for c in ("순매수거래대금", "순매수거래량", "거래대금")
                    if c in df.columns), None)
        if col is None:
            return None
        return pd.to_numeric(df[col], errors="coerce")

    parts = []
    for market in ("KOSPI", "KOSDAQ"):
        for investor in ("외국인", "기관합계"):
            s = _net(market, investor)
            if s is not None:
                parts.append(s)
    if not parts:
        return None
    net = pd.concat(parts, axis=1).fillna(0).sum(axis=1)

    try:
        from pykrx import stock
        cap = stock.get_market_cap(to, market="ALL")
        col = next((c for c in ("시가총액", "Marcap") if c in cap.columns), None)
        mc = pd.to_numeric(cap[col], errors="coerce") if col else None
        flow = (net / mc.reindex(net.index)).replace([np.inf, -np.inf], np.nan) \
            if mc is not None else net
    except Exception:
        flow = net
    return _zscore(flow)


def load_factor_panel(name: str) -> pd.DataFrame | None:
    """캐시된 팩터 패널 로드(커스텀 CSV 우선). 없으면 None."""
    cpath = _custom_csv(name)
    if os.path.exists(cpath):
        df = pd.read_csv(cpath, dtype={"code": str})
        df["date"] = pd.to_datetime(df["date"])
        return df.pivot(index="date", columns="code", values="score").sort_index()
    df = load_df(_cache_base(name))
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


class FactorProvider:
    """as_of 시점에 사용 가능한(직전) 팩터 스냅샷을 종목별로 제공."""

    def __init__(self, panel: pd.DataFrame | None):
        self.panel = panel.sort_index() if panel is not None and not panel.empty else None
        self.enabled = self.panel is not None

    def get(self, as_of: pd.Timestamp) -> pd.Series:
        if not self.enabled:
            return pd.Series(dtype=float)
        avail = self.panel.loc[:as_of]
        if avail.empty:
            return pd.Series(dtype=float)
        return avail.iloc[-1].dropna()
