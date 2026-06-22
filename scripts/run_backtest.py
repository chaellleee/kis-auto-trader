"""
백테스트 실행 → 시장(KOSPI) 대비 성과 리포트.

    python -m scripts.run_backtest

results/ 에 수익곡선 PNG 와 성과 CSV 를 저장한다.
실거래 전, 여기서 벤치마크를 유의미하게 이기는지 반드시 확인하세요.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.backtest.engine import Backtester
from src.config import build_strategy, load_config
from src.data import universe
from src.portfolio.manager import PortfolioManager
from src.utils.logger import get_logger

log = get_logger("run_backtest")


def _load_benchmark(index, name="KOSPI"):
    """벤치마크 지수 시계열. FinanceDataReader → pykrx → 균등지수 근사 순."""
    start = index.min().strftime("%Y-%m-%d")
    end = index.max().strftime("%Y-%m-%d")

    try:
        import FinanceDataReader as fdr
        sym = "KQ11" if str(name).upper().startswith("KOSDAQ") else "KS11"
        df = fdr.DataReader(sym, start, end)
        if df is not None and not df.empty and "Close" in df.columns:
            bm = df["Close"].copy()
            bm.index = pd.to_datetime(bm.index)
            return bm.reindex(index).ffill()
    except Exception as e:
        log.info("FDR 벤치마크 실패(%s) → pykrx 시도", e)

    try:
        from pykrx import stock
        ticker = "2001" if str(name).upper().startswith("KOSDAQ") else "1001"
        df = stock.get_index_ohlcv(index.min().strftime("%Y%m%d"),
                                   index.max().strftime("%Y%m%d"), ticker)
        if df is not None and not df.empty and "종가" in df.columns:
            bm = df["종가"].copy()
            bm.index = pd.to_datetime(bm.index)
            return bm.reindex(index).ffill()
    except Exception as e:
        log.warning("벤치마크 지수 로드 실패(%s). 동일가중 근사 사용.", e)
    return None


def main():
    cfg = load_config()
    close = universe.load_price_panel()
    log.info("가격 패널: %d일 x %d종목", *close.shape)

    benchmark = _load_benchmark(close.index, cfg["backtest"]["benchmark"])
    if benchmark is None:
        benchmark = close.mean(axis=1)

    strat = build_strategy(cfg)
    mgr = PortfolioManager(cfg)
    value_panel = universe.load_value_panel()
    bt = Backtester(cfg, strat, mgr, value_panel=value_panel)

    res = bt.run(close, benchmark)
    m = res["metrics"]

    print("\n" + "=" * 52)
    print("  백테스트 결과  (전략 vs 시장)")
    print("=" * 52)
    print(f"  전략 CAGR        : {m['CAGR']:8.2%}")
    print(f"  시장 CAGR        : {m['BM_CAGR']:8.2%}")
    print(f"  초과수익(연)     : {m['excess_CAGR']:8.2%}   ← 시장 대비 알파")
    print(f"  Sharpe           : {m['Sharpe']:8.2f}")
    print(f"  연변동성         : {m['Vol']:8.2%}")
    print(f"  최대낙폭(MDD)    : {m['MDD']:8.2%}")
    print(f"  승률(일간)       : {m['WinRate']:8.2%}")
    print(f"  Jensen Alpha(연) : {m['Alpha_ann']:8.2%}")
    print(f"  Beta             : {m['Beta']:8.2f}")
    print(f"  평균 회전율      : {m['AvgTurnover']:8.2f}")
    print(f"  최종 배수        : {m['FinalEquity']:8.2f}x")
    print("=" * 52 + "\n")

    os.makedirs("results", exist_ok=True)
    plt.figure(figsize=(11, 6))
    plt.plot(res["equity"].index, res["equity"].values, label="전략", lw=2)
    plt.plot(res["benchmark"].index, res["benchmark"].values,
             label="시장(벤치마크)", lw=1.5, ls="--")
    plt.title(f"누적수익  전략 CAGR {m['CAGR']:.1%} vs 시장 {m['BM_CAGR']:.1%}")
    plt.legend(); plt.grid(alpha=0.3); plt.ylabel("배수 (시작=1.0)")
    plt.tight_layout()
    plt.savefig("results/equity_curve.png", dpi=120)
    pd.DataFrame([m]).to_csv("results/metrics.csv", index=False)
    res["equity"].to_csv("results/equity.csv")
    log.info("결과 저장: results/equity_curve.png, results/metrics.csv")


if __name__ == "__main__":
    main()
