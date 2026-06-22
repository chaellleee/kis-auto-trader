"""
한국형 멀티팩터 전략 (Value + Low-Vol + Size + 수급 + Quality).

가격 모멘텀에서 피벗한 전략. 한국 시장에서 비교적 강건한 프리미엄을 결합한다.

  - value         : 저PER·저PBR·고배당 (펀더멘털 패널)        [핵심]
  - low_volatility: 최근 120일 일간수익률 변동성의 음수 (가격)  [핵심]
  - size          : 소형주 우위 (시가총액 패널)                [핵심]
  - supply_demand : 외국인+기관 순매수 흐름 (수급 패널)         [한국 특화]
  - quality       : ROE 프록시 (보조; 한국선 단독 효과 약함)

저변동성만 가격(close)에서 직접 계산하고, 나머지는 FactorProvider 패널에서 가져온다.
패널이 없으면(데이터 미수집/오프라인) 해당 팩터는 자동 비활성되고, 남은 팩터의
가중치를 재정규화해 동작한다. 즉 데이터가 전혀 없어도 저변동성 단일 팩터로는 돈다.

가격 모멘텀 전략과 달리 200일선 강제 필터를 끄는 것을 기본으로 한다(가치주는 종종
이동평균 아래에서 매수). 약세장 방어는 PortfolioManager 의 레짐 필터에 위임한다.
버퍼존(히스테리시스)으로 월별 회전율을 억제한다.
"""
import numpy as np
import pandas as pd

from .base import Strategy

_PRICE_FACTORS = {"low_volatility"}


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


class KoreaMultiFactor(Strategy):
    def __init__(self, config: dict, providers: dict | None = None):
        super().__init__(config)
        s = config["strategy"]
        self.weights = dict(s["factor_weights"])
        self.holding = int(s["holding"])
        self.ma_filter = int(s.get("ma_filter", 0))
        self.buffer_zone = float(s.get("buffer_zone", 1.0))
        self.vol_window = int(s.get("vol_window", 120))
        self.providers = providers or {}

    def _candidate_table(self, close: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        """as_of 까지 데이터로 후보 종목 + 가격기반 팩터(저변동성) 계산."""
        px = close.loc[:as_of]
        if len(px) < max(130, self.vol_window + 10):
            return pd.DataFrame()
        valid = px.columns[px.iloc[-1].notna() &
                           px.iloc[-self.vol_window:].notna().sum().ge(int(self.vol_window * 0.8))]
        px = px[valid].ffill()
        last = px.iloc[-1]
        low_vol = -px.iloc[-(self.vol_window + 1):].pct_change().iloc[-self.vol_window:].std(ddof=0)
        f = pd.DataFrame({"price": last, "low_volatility": low_vol}).dropna(subset=["low_volatility"])
        if self.ma_filter and self.ma_filter > 0 and len(px) >= self.ma_filter:
            ma = px.iloc[-self.ma_filter:].mean()
            f = f[f["price"] > ma.reindex(f.index)]
        return f

    def generate_signals(self, close: pd.DataFrame, as_of: pd.Timestamp,
                         current_holding=None) -> pd.Series:
        f = self._candidate_table(close, as_of)
        if f.empty:
            return pd.Series(dtype=float)

        score = pd.Series(0.0, index=f.index)
        used_w = 0.0
        for name, w in self.weights.items():
            if w == 0:
                continue
            if name in _PRICE_FACTORS:
                col = f.get(name)
            else:
                prov = self.providers.get(name)
                if prov is None or not getattr(prov, "enabled", False):
                    continue
                col = prov.get(as_of).reindex(f.index)
            if col is None or col.notna().sum() < 2:
                continue
            z = _zscore(col.fillna(col.mean()))
            score += w * z
            used_w += w

        if used_w == 0:
            return pd.Series(dtype=float)
        score = score / used_w

        ranked = score.sort_values(ascending=False)
        if current_holding and self.buffer_zone > 1.0:
            ext = set(ranked.head(int(self.holding * self.buffer_zone)).index)
            held_in_ext = [c for c in current_holding if c in ext]
            if held_in_ext:
                bonus = score.std(ddof=0) * 0.25 if score.std(ddof=0) > 0 else 0.01
                score = score.copy()
                score[held_in_ext] += bonus
                ranked = score.sort_values(ascending=False)

        top = ranked.head(self.holding)
        if top.empty:
            return pd.Series(dtype=float)
        return pd.Series(1.0 / len(top), index=top.index)
