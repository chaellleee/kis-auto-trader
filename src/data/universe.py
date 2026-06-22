"""
유니버스 구성 및 가격 데이터 수집.

KOSPI/KOSDAQ 전체 종목 리스트와 일봉 데이터를 가져온다.

데이터 소스 전략
----------------
- 종목 리스트/일봉의 '대량 과거 데이터'는 외부 소스로 받아 parquet 캐시에 저장한다.
  KIS API 일봉은 한 번에 ~100건이라 전 종목 백테스트엔 비효율적.
- 소스는 안정성 순으로 자동 폴백한다:
    1순위 FinanceDataReader (FDR)  — KRX 상장목록/일봉에 가장 안정적
    2순위 pykrx                    — FDR 실패 시 대체
  KRX 가 가끔 한쪽 소스를 막아도 다른 소스로 계속 동작한다.
- 새벽/휴장일처럼 '오늘' 데이터가 아직 없으면 자동으로 직전 영업일로 물러난다.
- 실시간 현재가/주문은 KIS API(Quotations/Trading)를 쓴다.
"""
import os
from datetime import datetime, timedelta

import pandas as pd

from ..utils.cacheio import load_df, save_df
from ..utils.logger import get_logger

log = get_logger("data.universe")

CACHE_DIR = os.path.join("data", "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "prices")
LIST_CACHE = os.path.join(CACHE_DIR, "universe")


def _ensure_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


_EXCLUDE_KW = ("ETN", "스팩", "리츠", "ETF", "우B", "우C")


def fetch_ticker_list(markets=("KOSPI", "KOSDAQ"),
                      ref_date: str | None = None) -> pd.DataFrame:
    """
    시장별 종목코드/이름/시장 구분을 DataFrame(code, name, market) 으로 반환.
    FinanceDataReader(재시도) → pykrx → 캐시(이전 목록/가격) 순으로 폴백한다.
    KRX/FDR 이 간헐적으로 빈 값을 주더라도 캐시로 진행할 수 있게 한다.
    """
    import time
    df = pd.DataFrame()
    for attempt in range(3):
        df = _tickers_fdr(markets)
        if not df.empty:
            break
        log.warning("FinanceDataReader 종목목록 비었음 (%d/3) → 재시도", attempt + 1)
        time.sleep(1.5 * (attempt + 1))

    if df.empty:
        log.warning("FDR 실패 → pykrx 로 재시도")
        df = _tickers_pykrx(markets, ref_date)

    if df.empty:
        df = _tickers_from_cache(markets)
        if not df.empty:
            log.warning("온라인 종목목록 실패 → 캐시된 목록 사용(%d종목)", len(df))

    _ensure_dir()
    if not df.empty:
        save_df(df, LIST_CACHE)
    log.info("유니버스 종목 수: %d", len(df))
    return df


def _tickers_from_cache(markets) -> pd.DataFrame:
    """이전에 저장한 종목목록, 없으면 가격 캐시의 종목코드로 복원."""
    cached = load_df(LIST_CACHE)
    if cached is not None and not cached.empty and "code" in cached.columns:
        return cached
    prices = load_df(PRICE_CACHE)
    if prices is not None and not prices.empty and "code" in prices.columns:
        codes = sorted(prices["code"].astype(str).str.zfill(6).unique())
        return pd.DataFrame({"code": codes, "name": codes, "market": "UNKNOWN"})
    return pd.DataFrame()


def _tickers_fdr(markets) -> pd.DataFrame:
    try:
        import FinanceDataReader as fdr
    except Exception:
        return pd.DataFrame()
    frames = []
    for mkt in markets:
        try:
            lst = fdr.StockListing(mkt)
        except Exception as e:
            log.warning("FDR %s 목록 실패: %s", mkt, e)
            continue
        if lst is None or lst.empty:
            continue
        code_col = next((c for c in ("Code", "Symbol") if c in lst.columns), None)
        name_col = next((c for c in ("Name",) if c in lst.columns), None)
        if not code_col or not name_col:
            continue
        sub = lst[[code_col, name_col]].rename(columns={code_col: "code", name_col: "name"})
        sub["market"] = mkt
        frames.append(sub)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[~df["name"].astype(str).str.contains("|".join(_EXCLUDE_KW), na=False)]
    return df.drop_duplicates("code").reset_index(drop=True)


def _tickers_pykrx(markets, ref_date) -> pd.DataFrame:
    try:
        from pykrx import stock
    except Exception:
        return pd.DataFrame()
    ref_date = _nearest_business_day(ref_date)
    frames = []
    for mkt in markets:
        try:
            codes = stock.get_market_ticker_list(ref_date, market=mkt)
        except Exception as e:
            log.warning("pykrx %s 목록 실패: %s", mkt, e)
            continue
        for code in codes:
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:
                continue
            if any(k in str(name) for k in _EXCLUDE_KW):
                continue
            frames.append({"code": str(code).zfill(6), "name": name, "market": mkt})
    return pd.DataFrame(frames)


def _nearest_business_day(ref_date: str | None) -> str:
    """ref_date(또는 오늘)부터 거꾸로 최대 10일 내 유효 영업일 문자열(YYYYMMDD)."""
    base = datetime.strptime(ref_date, "%Y%m%d") if ref_date else datetime.now()
    try:
        from pykrx import stock
        for back in range(0, 10):
            d = (base - timedelta(days=back)).strftime("%Y%m%d")
            try:
                if stock.get_market_ticker_list(d, market="KOSPI"):
                    return d
            except Exception:
                continue
    except Exception:
        pass
    d = base
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_prices(codes, start: str, end: str | None = None,
                 incremental: bool = True) -> pd.DataFrame:
    """
    종목들의 일봉을 long-format DataFrame 으로 반환/캐싱.
    columns: date, code, open, high, low, close, volume, value(거래대금)
    FinanceDataReader → pykrx 순으로 종목별 폴백한다.
    """
    end = end or datetime.now().strftime("%Y%m%d")
    _ensure_dir()

    cached = pd.DataFrame()
    if incremental:
        prev = load_df(PRICE_CACHE)
        if prev is not None and not prev.empty:
            cached = prev
            log.info("기존 캐시 로드: %d rows", len(cached))

    frames = [cached] if not cached.empty else []
    fail = 0
    from tqdm import tqdm
    for code in tqdm(codes, desc="일봉 수집"):
        c_start = start
        if not cached.empty:
            have = cached[cached["code"] == code]
            if not have.empty:
                last = pd.to_datetime(have["date"]).max()
                c_start = (last + timedelta(days=1)).strftime("%Y%m%d")
                if c_start > end:
                    continue
        df = _ohlcv_one(code, c_start, end)
        if df is None or df.empty:
            fail += 1
            continue
        frames.append(df)

    if len(frames) <= (1 if not cached.empty else 0):
        log.error("일봉 데이터를 한 건도 받지 못했습니다(실패 %d). 네트워크/소스를 확인하세요.", fail)
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    out = out.drop_duplicates(["date", "code"]).sort_values(["code", "date"])
    save_df(out, PRICE_CACHE)
    log.info("가격 캐시 저장: %d rows, %d 종목 (수집실패 %d)",
             len(out), out["code"].nunique(), fail)
    return out


def _ohlcv_one(code: str, start: str, end: str) -> pd.DataFrame | None:
    """종목 1개 일봉. FDR 우선, 실패 시 pykrx."""
    code = str(code).zfill(6)
    try:
        import FinanceDataReader as fdr
        s = f"{start[:4]}-{start[4:6]}-{start[6:]}" if len(start) == 8 else start
        e = f"{end[:4]}-{end[4:6]}-{end[6:]}" if len(end) == 8 else end
        df = fdr.DataReader(code, s, e)
        if df is not None and not df.empty:
            df = df.reset_index().rename(columns={
                "Date": "date", "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume",
            })
            df["code"] = code
            if "value" not in df.columns:
                df["value"] = df["close"] * df["volume"]
            return df[["date", "code", "open", "high", "low", "close", "volume", "value"]]
    except Exception:
        pass
    try:
        from pykrx import stock
        df = stock.get_market_ohlcv(start, end, code)
        if df is not None and not df.empty:
            df = df.reset_index().rename(columns={
                "날짜": "date", "시가": "open", "고가": "high", "저가": "low",
                "종가": "close", "거래량": "volume", "거래대금": "value",
            })
            df["code"] = code
            cols = ["date", "code", "open", "high", "low", "close", "volume"]
            if "value" in df.columns:
                cols.append("value")
            return df[cols]
    except Exception:
        pass
    return None


def load_price_panel() -> pd.DataFrame:
    """캐시된 가격 데이터를 종가 wide-format(행=날짜, 열=종목)으로 변환."""
    df = load_df(PRICE_CACHE)
    if df is None or df.empty:
        raise FileNotFoundError(
            "가격 캐시가 없습니다. 먼저 `python -m scripts.fetch_universe` 를 실행하세요."
        )
    close = df.pivot(index="date", columns="code", values="close").sort_index()
    return close


def load_value_panel() -> pd.DataFrame | None:
    """거래대금 wide-format (유동성 필터용). 없으면 None."""
    df = load_df(PRICE_CACHE)
    if df is None or df.empty or "value" not in df.columns:
        return None
    return df.pivot(index="date", columns="code", values="value").sort_index()


def apply_liquidity_filter(close: pd.DataFrame, value: pd.DataFrame | None,
                           min_avg_value: float, top_n: int) -> list[str]:
    """최근 20일 평균 거래대금 기준 유동성 상위 종목 선별(전역, 마지막 시점 기준)."""
    if value is None or value.empty:
        return list(close.columns)
    avg_val = value.tail(20).mean()
    liquid = avg_val[avg_val >= min_avg_value].sort_values(ascending=False)
    selected = list(liquid.head(top_n).index)
    log.info("유동성 필터 후 후보 종목: %d", len(selected))
    return selected


def liquid_as_of(value: pd.DataFrame | None, as_of, top_n: int,
                 min_avg_value: float = 0.0, lookback: int = 60) -> list[str]:
    """
    as_of 시점까지의 거래대금으로 유동성 상위 종목을 선별(룩어헤드 방지).

    거래가 거의 없는 동전주·우선주가 '저변동성'으로 잘못 선택되는 걸 막는다.
    value 가 없으면 빈 리스트(=필터 미적용 신호).
    """
    if value is None or value.empty or top_n <= 0:
        return []
    win = value.loc[:as_of].tail(lookback)
    if win.empty:
        return []
    avg_val = win.mean()
    avg_val = avg_val[avg_val >= min_avg_value]
    return list(avg_val.sort_values(ascending=False).head(top_n).index)
