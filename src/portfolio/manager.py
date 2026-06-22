"""
포지션 사이징 및 리스크 관리.

전략이 만든 '동일가중 목표비중'을 받아서:
  1) 변동성 타게팅: 종목별 변동성의 역수로 가중 (저변동성에 더 많이)
  2) 종목당 비중 상한 적용
  3) 시장 레짐 필터: 코스피가 200일선 아래면 전체 익스포저 축소(현금↑)
  4) 현금 버퍼 확보
최종 목표비중(Series)을 반환한다.
"""
import numpy as np
import pandas as pd

from ..utils.logger import get_logger

log = get_logger("portfolio.manager")


class PortfolioManager:
    def __init__(self, config: dict):
        r = config["risk"]
        self.max_weight = float(r["max_weight_per_stock"])
        self.use_vol_targeting = bool(r["use_vol_targeting"])
        self.target_vol = float(r["target_portfolio_vol"])
        self.regime_filter = bool(r["market_regime_filter"])
        self.regime_exposure = float(r.get("regime_exposure", 0.5))
        self.cash_buffer = float(r["cash_buffer"])
        self.turnover_limit = float(config.get("execution", {}).get("turnover_limit", 0.0))

    @staticmethod
    def _cap_weights(w: pd.Series, cap: float, iters: int = 50) -> pd.Series:
        """합=1 을 유지하며 모든 비중이 cap 이하가 되도록 반복 조정."""
        w = w.clip(lower=0).astype(float)
        if w.sum() <= 0:
            return w
        w = w / w.sum()
        if cap * len(w) < 1.0:
            return pd.Series(1.0 / len(w), index=w.index)
        for _ in range(iters):
            over = w > cap + 1e-12
            if not over.any():
                break
            excess = (w[over] - cap).sum()
            w[over] = cap
            under = ~over
            base = w[under].sum()
            if base <= 0:
                break
            w[under] += excess * (w[under] / base)
        return w

    def apply_turnover_limit(self, old: pd.Series, target: pd.Series) -> pd.Series:
        """
        목표비중으로의 이동을 단방향 회전율 상한 이내로 부분 반영한다.

        단방향 회전율 = 0.5 * Σ|target - old|.
        상한을 넘으면 old + α·(target - old) 로 비례 축소(α<=1).
        결과적으로 '목표에 가까워지되 한 번에 일부만 교체'해 거래세·수수료 누수를 막는다.
        """
        if self.turnover_limit <= 0:
            return target
        old = old.fillna(0) if old is not None else pd.Series(dtype=float)
        allc = old.index.union(target.index)
        o = old.reindex(allc).fillna(0.0)
        t = target.reindex(allc).fillna(0.0)
        one_way = 0.5 * (t - o).abs().sum()
        if one_way <= self.turnover_limit or one_way == 0:
            return target
        alpha = self.turnover_limit / one_way
        blended = o + alpha * (t - o)
        return blended[blended > 1e-6]

    def size_positions(self, target: pd.Series, close: pd.DataFrame,
                       as_of: pd.Timestamp,
                       benchmark: pd.Series | None = None) -> pd.Series:
        if target.empty:
            return target

        px = close.loc[:as_of, target.index].ffill()
        rets = px.pct_change().iloc[-120:]

        if self.use_vol_targeting:
            vol = rets.std(ddof=0).replace(0, np.nan)
            inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0)
            if inv.sum() > 0:
                w = inv / inv.sum()
            else:
                w = target.copy()
        else:
            w = target.copy()

        w = self._cap_weights(w, self.max_weight)

        gross = 1.0
        if self.use_vol_targeting:
            cov = rets.cov()
            port_var = float(w.values @ cov.values @ w.values)
            port_vol = np.sqrt(port_var * 252) if port_var > 0 else self.target_vol
            if port_vol > 0:
                gross = min(1.0, self.target_vol / port_vol)

        regime = 1.0
        if self.regime_filter and benchmark is not None:
            bm = benchmark.loc[:as_of].dropna()
            if len(bm) > 200:
                ma200 = bm.rolling(200).mean().iloc[-1]
                if bm.iloc[-1] < ma200:
                    regime = self.regime_exposure
                    log.debug("약세장 감지 → 익스포저 %.0f%%", regime * 100)

        exposure = gross * regime * (1.0 - self.cash_buffer)
        final = w * exposure
        return final
