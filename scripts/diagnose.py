"""
파이프라인 전 구간 진단 — '왜 결과가 안 바뀌나'를 한 번에 확인.

    python -m scripts.diagnose

아래 3가지 체크포인트의 실제 상태를 출력하고, 마지막에 '지금 코드'로
신선한 백테스트를 돌려 CAGR·상승장 참여율을 보여준다.
이 출력을 그대로 복사해 공유하면 막힌 지점을 정확히 짚을 수 있다.
"""
import os
from datetime import datetime

import pandas as pd

from src.config import build_strategy, load_config
from src.data import universe


def _ts(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%m/%d %H:%M") \
        if os.path.exists(path) else "없음"


def main():
    print("=" * 64)
    print("  KIS Auto Trader — 파이프라인 진단")
    print("=" * 64)

    cfg = load_config()
    s = cfg["strategy"]
    print("\n[1] 설정(config.yaml)")
    print(f"    전략 이름      : {s['name']}")
    print(f"    리밸런싱       : {s['rebalance']}")
    print(f"    팩터 가중치    : {s.get('factor_weights')}")
    print(f"    top_n_liquid   : {cfg['universe'].get('top_n_liquid')}  (유동성 필터)")

    print("\n[2] 데이터 캐시(data/cache)")
    cache = "data/cache"
    for f in ("prices.parquet", "prices.pkl", "universe.parquet"):
        p = os.path.join(cache, f)
        if os.path.exists(p):
            print(f"    {f:22} 수정 {_ts(p)}")
    print("    --- 멀티팩터 패널(factor_*) ---")
    found_factor = False
    for fac in ("value", "quality", "size", "supply_demand"):
        for ext in ("parquet", "pkl"):
            p = os.path.join(cache, f"factor_{fac}.{ext}")
            if os.path.exists(p):
                panel = universe.load_df(os.path.join(cache, f"factor_{fac}"))
                shape = panel.shape if panel is not None else "?"
                print(f"    factor_{fac:14} 수정 {_ts(p)} | shape {shape}")
                found_factor = True
    if not found_factor:
        print("    ❌ factor_* 패널이 하나도 없습니다 → 가치/사이즈/수급 팩터 비활성")
        print("       (pykrx 가 KRX 에 못 붙은 것. 저변동성 단일 팩터로만 동작)")

    print("\n[3] 전략 빌드 결과(실제 활성 팩터)")
    strat = build_strategy(cfg)
    print(f"    전략 클래스    : {type(strat).__name__}")
    if hasattr(strat, "providers"):
        active = {k: v.enabled for k, v in strat.providers.items()}
        print(f"    패널 provider  : {active}")
    close = universe.load_price_panel()
    val = universe.load_value_panel()
    print(f"    가격 패널      : {close.shape[0]}일 x {close.shape[1]}종목")
    print(f"    거래대금 패널  : {'있음' if val is not None else '없음(유동성 필터 불가!)'}")
    top_n = int(cfg["universe"].get("top_n_liquid", 0) or 0)
    if val is not None and top_n:
        liq = universe.liquid_as_of(val, close.index[-1], top_n)
        print(f"    유동성 필터 후 : {len(liq)}종목 (전체 {close.shape[1]} 중)")

    print("\n[4] 지금 코드로 신선한 백테스트 (캐시된 옛 PNG 무시)")
    from src.backtest.engine import Backtester
    from src.portfolio.manager import PortfolioManager
    bench = close.mean(axis=1)
    bt = Backtester(cfg, strat, PortfolioManager(cfg), value_panel=val)
    res = bt.run(close, bench)
    m = res["metrics"]
    eq = res["equity"]
    e20 = eq.loc["2020-01-01":"2021-12-31"]
    bull = (e20.iloc[-1] / e20.iloc[0] - 1) if len(e20) > 1 else float("nan")
    print(f"    CAGR           : {m['CAGR']:.2%}")
    print(f"    시장 대비 초과 : {m['excess_CAGR']:.2%}")
    print(f"    MDD            : {m['MDD']:.2%}")
    print(f"    2020~2021 참여 : {bull:.2%}   ← -2% 면 옛 버그, +20%대면 정상")
    print("=" * 64)
    if m["CAGR"] > 0 and bull > 0.1:
        print("  ✅ 정상 동작. 이전에 보신 -2.7% 차트는 옛 결과 파일입니다.")
        print("     results/ 의 옛 PNG 를 지우고 run_backtest 를 다시 보세요.")
    else:
        print("  ⚠️ 여전히 비정상. 위 [2]/[3] 의 어디가 비었는지 확인 필요.")
    print("=" * 64)


if __name__ == "__main__":
    main()
