"""
벡터화 백테스트 엔진.

전략 + 포트폴리오 매니저를 주기적으로 호출해 목표비중을 만들고,
거래비용/세금/슬리피지를 반영해 일별 포트폴리오 수익을 계산한다.
벤치마크(KOSPI) 대비 초과수익(알파)을 함께 산출한다.
"""
import numpy as np
import pandas as pd

from ..data import universe
from ..utils.logger import get_logger

log = get_logger("backtest")

_REBAL = {"daily": 1, "weekly": 5, "monthly": 21}


class Backtester:
    def __init__(self, config: dict, strategy, manager, value_panel=None):
        self.cfg = config
        self.strategy = strategy
        self.manager = manager
        c = config["costs"]
        self.cost = c["commission"] + c["slippage"]
        self.tax = c["tax"]
        self.rebal = _REBAL.get(config["strategy"]["rebalance"], 5)
        self.value_panel = value_panel
        u = config.get("universe", {})
        self.top_n_liquid = int(u.get("top_n_liquid", 0) or 0)
        self.min_avg_value = float(u.get("min_avg_value", 0) or 0)

    def run(self, close: pd.DataFrame, benchmark: pd.Series) -> dict:
        cfg = self.cfg["backtest"]
        idx = close.loc[cfg["start"]:cfg["end"]].index
        if len(idx) < 260:
            raise ValueError("백테스트 구간 데이터가 부족합니다 (>=260 영업일 필요).")

        daily_ret = close.pct_change().fillna(0.0)
        weights = pd.Series(dtype=float)
        equity = [1.0]
        dates = [idx[0]]
        turnover_log = []

        start_i = max(260, self.rebal)
        try:
            from tqdm import tqdm
            pbar = tqdm(total=(len(idx) - start_i) // self.rebal + 1,
                        desc="백테스트 리밸런싱", unit="rebal")
        except Exception:
            pbar = None

        for i in range(start_i, len(idx)):
            today = idx[i]

            if not weights.empty:
                cols = [c for c in weights.index if c in daily_ret.columns]
                r = float((daily_ret.loc[today, cols] * weights[cols]).sum())
            else:
                r = 0.0
            equity.append(equity[-1] * (1 + r))
            dates.append(today)

            if (i - start_i) % self.rebal == 0:
                holding = set(weights.index) if not weights.empty else None
                cand = close
                if self.value_panel is not None and self.top_n_liquid > 0:
                    liq = universe.liquid_as_of(self.value_panel, today,
                                                self.top_n_liquid, self.min_avg_value)
                    if liq:
                        cols = [c for c in liq if c in close.columns]
                        if cols:
                            cand = close[cols]
                target = self.strategy.generate_signals(cand, today, current_holding=holding)
                target = self.manager.size_positions(target, close, today, benchmark)
                target = self.manager.apply_turnover_limit(weights, target)
                turnover = self._turnover(weights, target)
                cost = turnover * self.cost + self._sell_turnover(weights, target) * self.tax
                equity[-1] *= (1 - cost)
                turnover_log.append(turnover)
                weights = target
                if pbar is not None:
                    pbar.update(1)

        if pbar is not None:
            pbar.close()

        eq = pd.Series(equity, index=pd.DatetimeIndex(dates)).iloc[1:]
        bm = benchmark.reindex(eq.index).ffill()
        bm_norm = bm / bm.iloc[0]

        metrics = self._metrics(eq, bm_norm, np.mean(turnover_log) if turnover_log else 0)
        return {"equity": eq, "benchmark": bm_norm, "metrics": metrics}

    @staticmethod
    def _turnover(old: pd.Series, new: pd.Series) -> float:
        allc = old.index.union(new.index)
        o = old.reindex(allc).fillna(0)
        n = new.reindex(allc).fillna(0)
        return float((n - o).abs().sum())

    @staticmethod
    def _sell_turnover(old: pd.Series, new: pd.Series) -> float:
        allc = old.index.union(new.index)
        o = old.reindex(allc).fillna(0)
        n = new.reindex(allc).fillna(0)
        return float((o - n).clip(lower=0).sum())

    def _metrics(self, eq: pd.Series, bm: pd.Series, avg_turnover: float) -> dict:
        rets = eq.pct_change().dropna()
        years = len(eq) / 252
        cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
        ann_vol = rets.std() * np.sqrt(252)
        sharpe = (rets.mean() * 252) / ann_vol if ann_vol > 0 else 0
        mdd = (eq / eq.cummax() - 1).min()
        win = (rets > 0).mean()

        bm_rets = bm.pct_change().dropna()
        bm_cagr = bm.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
        common = rets.index.intersection(bm_rets.index)
        x, y = bm_rets.reindex(common).fillna(0), rets.reindex(common).fillna(0)
        beta = np.cov(y, x)[0, 1] / np.var(x) if np.var(x) > 0 else 0
        alpha_ann = (y.mean() - beta * x.mean()) * 252

        return {
            "CAGR": cagr, "BM_CAGR": bm_cagr, "excess_CAGR": cagr - bm_cagr,
            "Sharpe": sharpe, "MDD": mdd, "Vol": ann_vol,
            "WinRate": win, "Alpha_ann": alpha_ann, "Beta": beta,
            "AvgTurnover": avg_turnover, "FinalEquity": eq.iloc[-1],
        }
