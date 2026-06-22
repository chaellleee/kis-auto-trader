"""
다중 팩터 모멘텀 + 퀄리티 전략.

5개 팩터를 각각 횡단면 z-score 로 표준화한 뒤 가중합해 종합점수를 만든다.

  1) momentum_12_1 : 252영업일 전 대비 21영업일 전 수익률 (최근 1개월 제외)
                     → 모멘텀 효과를 잡되 단기 반전(reversal)을 회피
  2) momentum_3m   : 최근 63영업일 수익률 (단기 추세)
  3) low_volatility: 최근 120영업일 일간수익률 표준편차의 '음수'
  4) trend         : (현재가 / 200일 이동평균 - 1) → 추세 강도
  5) quality       : ROE/이익수익률 기반 재무 우량성 (펀더멘털)
                     → 가격팩터와 상관이 낮아 횡보·가치장에서 분산효과.
                       펀더멘털 데이터가 없으면 자동으로 비활성(가중치 0).

필터
  - 200일 이동평균선 위 종목만 (장기 하락추세 제외)
  - 12-1 모멘텀이 음수면 제외 (절대 모멘텀)

버퍼존(Buffer Zone)
  - 매주 순위가 살짝 밀렸다고 곧바로 갈아타면 회전율·세금이 누수된다.
  - 현재 보유 종목이 'holding × buffer_zone' 순위 안에 있으면 약한 점수 보너스를 줘
    경계선 신규 종목보다 우선 유지 → 잦은 교체(휘프소)를 줄인다.
"""
import numpy as np
import pandas as pd

from .base import Strategy


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


class MultiFactorMomentum(Strategy):
    def __init__(self, config: dict, quality_provider=None):
        super().__init__(config)
        s = config["strategy"]
        self.weights = dict(s["factor_weights"])
        self.holding = int(s["holding"])
        self.ma_filter = int(s["ma_filter"])
        self.min_momentum = float(s["min_momentum"])
        self.buffer_zone = float(s.get("buffer_zone", 1.0))
        self.quality_provider = quality_provider

    def _factors(self, close: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        px = close.loc[:as_of].copy()
        if len(px) < 260:
            return pd.DataFrame()

        valid = px.columns[px.iloc[-1].notna() & px.iloc[-252:].notna().sum().ge(200)]
        px = px[valid].ffill()
        last = px.iloc[-1]

        mom_12_1 = (px.iloc[-21] / px.iloc[-252]) - 1.0
        mom_3m = (last / px.iloc[-63]) - 1.0
        low_vol = -px.iloc[-121:].pct_change().iloc[-120:].std(ddof=0)
        ma = px.iloc[-self.ma_filter:].mean()
        trend = (last / ma) - 1.0

        f = pd.DataFrame({
            "momentum_12_1": mom_12_1,
            "momentum_3m": mom_3m,
            "low_volatility": low_vol,
            "trend": trend,
            "price": last,
            "ma": ma,
        }).dropna(subset=["momentum_12_1", "momentum_3m", "low_volatility", "trend"])

        if self.quality_provider is not None and getattr(self.quality_provider, "enabled", False):
            q = self.quality_provider.get(as_of)
            f["quality"] = q.reindex(f.index)
        return f

    def generate_signals(self, close: pd.DataFrame, as_of: pd.Timestamp,
                         current_holding=None) -> pd.Series:
        f = self._factors(close, as_of)
        if f.empty:
            return pd.Series(dtype=float)

        f = f[(f["price"] > f["ma"]) & (f["momentum_12_1"] > self.min_momentum)]
        if f.empty:
            return pd.Series(dtype=float)

        score = pd.Series(0.0, index=f.index)
        used_w = 0.0
        for name, w in self.weights.items():
            if name not in f.columns:
                continue
            col = f[name]
            if col.notna().sum() < 2:
                continue
            z = _zscore(col.fillna(col.mean()))
            score += w * z
            used_w += w
        if used_w > 0:
            score = score / used_w

        ranked = score.sort_values(ascending=False)
        if current_holding and self.buffer_zone > 1.0:
            ext_n = int(self.holding * self.buffer_zone)
            ext_set = set(ranked.head(ext_n).index)
            held_in_ext = [c for c in current_holding if c in ext_set]
            if held_in_ext:
                bonus = score.std(ddof=0) * 0.25 if score.std(ddof=0) > 0 else 0.01
                score = score.copy()
                score[held_in_ext] += bonus
                ranked = score.sort_values(ascending=False)

        top = ranked.head(self.holding)
        if top.empty:
            return pd.Series(dtype=float)
        return pd.Series(1.0 / len(top), index=top.index)
