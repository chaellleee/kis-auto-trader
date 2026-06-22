"""
DataFrame 캐시 저장/로드 헬퍼.

parquet(pyarrow/fastparquet) 가 설치돼 있으면 parquet 으로,
없으면 자동으로 pickle(.pkl) 로 저장/로드한다.
경로는 확장자 없는 'base' 를 받는다. 예: data/cache/prices
"""
import os

import pandas as pd

from .logger import get_logger

log = get_logger("cacheio")


def save_df(df: pd.DataFrame, base: str) -> str:
    """base(확장자 없음)에 저장. parquet 우선, 실패 시 pickle. 저장 경로 반환."""
    os.makedirs(os.path.dirname(base), exist_ok=True)
    pq = base + ".parquet"
    try:
        df.to_parquet(pq, index=False)
        return pq
    except Exception as e:
        pkl = base + ".pkl"
        df.to_pickle(pkl)
        log.info("parquet 엔진이 없어 pickle 로 저장했습니다(%s). "
                 "더 빠른 저장을 원하면 `pip install pyarrow` 를 권장합니다.", os.path.basename(pkl))
        return pkl


def load_df(base: str) -> pd.DataFrame | None:
    """base 의 parquet 또는 pkl 을 로드. 둘 다 없으면 None."""
    pq, pkl = base + ".parquet", base + ".pkl"
    if os.path.exists(pq):
        try:
            return pd.read_parquet(pq)
        except Exception:
            pass
    if os.path.exists(pkl):
        return pd.read_pickle(pkl)
    return None


def exists(base: str) -> bool:
    return os.path.exists(base + ".parquet") or os.path.exists(base + ".pkl")
