"""
전략·포트폴리오·백테스트 엔진 스모크 테스트 (외부 API/네트워크 불필요).

가상의 가격 패널을 만들어 신호 생성 → 사이징 → 백테스트가
에러 없이 도는지, 출력 형태가 올바른지 확인한다.

    python -m pytest tests/ -v
    또는
    python tests/test_strategy.py
"""
import numpy as np
import pandas as pd

from src.backtest.engine import Backtester
from src.config import build_strategy
from src.portfolio.manager import PortfolioManager
from src.strategy.multifactor_momentum import MultiFactorMomentum


def _fake_config():
    return {
        "strategy": {
            "name": "multifactor_momentum", "rebalance": "weekly", "holding": 10,
            "factor_weights": {"momentum_12_1": 0.30, "momentum_3m": 0.15,
                               "low_volatility": 0.20, "trend": 0.20, "quality": 0.15},
            "buffer_zone": 1.5, "ma_filter": 200, "min_momentum": 0.0,
        },
        "execution": {"turnover_limit": 0.2},
        "risk": {"max_weight_per_stock": 0.10, "use_vol_targeting": True,
                 "target_portfolio_vol": 0.15, "stop_loss": -0.08, "take_profit": 0.25,
                 "market_regime_filter": True, "cash_buffer": 0.05},
        "costs": {"commission": 0.00015, "tax": 0.0018, "slippage": 0.001},
        "backtest": {"start": "2018-01-01", "end": "2021-12-31",
                     "initial_cash": 10000000, "benchmark": "KOSPI"},
    }


def _fake_korea_config():
    """한국형 멀티팩터 설정 (패널 없이 저변동성만으로도 돌아야 함)."""
    return {
        "strategy": {
            "name": "korea_multifactor", "rebalance": "monthly", "holding": 12,
            "factor_weights": {"value": 0.30, "low_volatility": 0.25, "size": 0.20,
                               "supply_demand": 0.15, "quality": 0.10},
            "buffer_zone": 1.3, "vol_window": 120, "ma_filter": 0,
        },
        "execution": {"turnover_limit": 0.3},
        "risk": {"max_weight_per_stock": 0.08, "use_vol_targeting": True,
                 "target_portfolio_vol": 0.16, "stop_loss": -0.15, "take_profit": 0.60,
                 "market_regime_filter": True, "regime_exposure": 0.7, "cash_buffer": 0.03},
        "costs": {"commission": 0.00015, "tax": 0.0018, "slippage": 0.001},
        "backtest": {"start": "2018-01-01", "end": "2021-12-31",
                     "initial_cash": 10000000, "benchmark": "KOSPI"},
    }


def _fake_prices(n_days=900, n_stocks=40, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-06-01", periods=n_days)
    out = {}
    for k in range(n_stocks):
        drift = rng.normal(0.0003, 0.0004)
        vol = rng.uniform(0.01, 0.03)
        shocks = rng.normal(drift, vol, n_days)
        price = 10000 * np.exp(np.cumsum(shocks))
        out[f"{k:06d}"] = price
    return pd.DataFrame(out, index=dates)


def test_signals_shape():
    cfg = _fake_config()
    close = _fake_prices()
    strat = MultiFactorMomentum(cfg)
    w = strat.generate_signals(close, close.index[-1])
    assert isinstance(w, pd.Series)
    assert len(w) <= cfg["strategy"]["holding"]
    if not w.empty:
        assert abs(w.sum() - 1.0) < 1e-6


def test_position_sizing_respects_cap():
    cfg = _fake_config()
    close = _fake_prices()
    strat = build_strategy(cfg)
    mgr = PortfolioManager(cfg)
    as_of = close.index[-1]
    w = strat.generate_signals(close, as_of)
    sized = mgr.size_positions(w, close, as_of, close.mean(axis=1))
    if not sized.empty:
        assert sized.max() <= cfg["risk"]["max_weight_per_stock"] + 1e-9
        assert sized.sum() <= 1.0 + 1e-9


def test_turnover_limit_caps_trading():
    cfg = _fake_config()
    mgr = PortfolioManager(cfg)
    old = pd.Series({"A": 0.5, "B": 0.5})
    target = pd.Series({"C": 0.5, "D": 0.5})
    limited = mgr.apply_turnover_limit(old, target)
    allc = old.index.union(limited.index)
    o = old.reindex(allc).fillna(0)
    t = limited.reindex(allc).fillna(0)
    one_way = 0.5 * (t - o).abs().sum()
    assert one_way <= cfg["execution"]["turnover_limit"] + 1e-9


def test_buffer_zone_reduces_turnover():
    """버퍼존을 켜면 보유종목 유지가 늘어 회전율이 낮아져야 한다."""
    cfg = _fake_config()
    close = _fake_prices(seed=7)
    as_of = close.index[-1]

    cfg_off = {**cfg, "strategy": {**cfg["strategy"], "buffer_zone": 1.0}}
    s_off = MultiFactorMomentum(cfg_off)
    s_on = MultiFactorMomentum(cfg)

    base = s_off.generate_signals(close, as_of)
    held = set(base.index)
    later = close.index[-2]
    off_next = set(s_off.generate_signals(close, later).index)
    on_next = set(s_on.generate_signals(close, later, current_holding=held).index)
    keep_off = len(held & off_next)
    keep_on = len(held & on_next)
    assert keep_on >= keep_off


def test_quality_disabled_when_no_provider():
    """퀄리티 provider 가 없어도 나머지 팩터로 정상 동작."""
    cfg = _fake_config()
    close = _fake_prices()
    s = MultiFactorMomentum(cfg, quality_provider=None)
    w = s.generate_signals(close, close.index[-1])
    assert isinstance(w, pd.Series)


def test_korea_strategy_price_only():
    """패널(value/size/수급) 없이도 저변동성 단일 팩터로 신호가 나와야 한다."""
    from src.strategy.korea_multifactor import KoreaMultiFactor
    cfg = _fake_korea_config()
    close = _fake_prices()
    s = KoreaMultiFactor(cfg, providers={})
    w = s.generate_signals(close, close.index[-1])
    assert isinstance(w, pd.Series)
    assert len(w) <= cfg["strategy"]["holding"]
    if not w.empty:
        assert abs(w.sum() - 1.0) < 1e-6


def test_korea_strategy_with_panel():
    """팩터 패널(provider)이 있으면 점수에 반영돼 동작한다."""
    from src.data.factors import FactorProvider
    from src.strategy.korea_multifactor import KoreaMultiFactor
    cfg = _fake_korea_config()
    close = _fake_prices()
    codes = list(close.columns)
    dates = pd.date_range(close.index[200], close.index[-1], freq="ME")
    rng = np.random.default_rng(1)
    panel = pd.DataFrame(rng.normal(size=(len(dates), len(codes))),
                         index=dates, columns=codes)
    s = KoreaMultiFactor(cfg, providers={"value": FactorProvider(panel)})
    w = s.generate_signals(close, close.index[-1], current_holding=set(codes[:5]))
    assert isinstance(w, pd.Series) and len(w) <= cfg["strategy"]["holding"]


def test_korea_backtest_runs():
    from src.backtest.engine import Backtester
    from src.portfolio.manager import PortfolioManager
    from src.strategy.korea_multifactor import KoreaMultiFactor
    cfg = _fake_korea_config()
    close = _fake_prices()
    bt = Backtester(cfg, KoreaMultiFactor(cfg, providers={}), PortfolioManager(cfg))
    res = bt.run(close, close.mean(axis=1))
    assert res["equity"].iloc[-1] > 0


def test_factor_provider_asof():
    """FactorProvider 는 as_of 이하 직전 스냅샷만 반환(룩어헤드 방지)."""
    from src.data.factors import FactorProvider
    idx = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
    panel = pd.DataFrame({"005930": [1.0, 2.0, 3.0]}, index=idx)
    p = FactorProvider(panel)
    assert p.enabled
    assert p.get(pd.Timestamp("2020-02-15"))["005930"] == 1.0
    assert p.get(pd.Timestamp("2020-03-31"))["005930"] == 3.0
    assert FactorProvider(None).get(pd.Timestamp("2020-01-01")).empty


def test_market_phase():
    from datetime import datetime
    from src.trader import market_phase
    cfg = {"live": {"market_open": "09:00", "market_close": "15:20"}}
    assert market_phase(datetime(2026, 6, 19, 8, 30), cfg) == "before_open"
    assert market_phase(datetime(2026, 6, 19, 9, 5), cfg) == "open"
    assert market_phase(datetime(2026, 6, 19, 15, 30), cfg) == "after_close"
    assert market_phase(datetime(2026, 6, 20, 11, 0), cfg) == "weekend"


def test_backtest_runs():
    cfg = _fake_config()
    close = _fake_prices()
    bt = Backtester(cfg, build_strategy(cfg), PortfolioManager(cfg))
    res = bt.run(close, close.mean(axis=1))
    m = res["metrics"]
    assert "CAGR" in m and "Sharpe" in m and "MDD" in m
    assert res["equity"].iloc[-1] > 0
    print("\nsmoke metrics:", {k: round(v, 4) for k, v in m.items()})


if __name__ == "__main__":
    test_signals_shape()
    test_position_sizing_respects_cap()
    test_turnover_limit_caps_trading()
    test_buffer_zone_reduces_turnover()
    test_quality_disabled_when_no_provider()
    test_korea_strategy_price_only()
    test_korea_strategy_with_panel()
    test_korea_backtest_runs()
    test_factor_provider_asof()
    test_market_phase()
    test_backtest_runs()
    print("\n✅ 모든 스모크 테스트 통과")
