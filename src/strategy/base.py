"""전략 베이스 클래스. 새 전략은 generate_signals 만 구현하면 된다."""
from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def generate_signals(self, close: pd.DataFrame, as_of: pd.Timestamp,
                         current_holding=None) -> pd.Series:
        """
        as_of 시점 기준 목표 포트폴리오(종목 -> 목표비중)를 반환.
        비중 합 <= 1 (나머지는 현금). 매수 후보가 없으면 빈 Series.
        current_holding: 현재 보유 종목코드 집합(버퍼존 적용용, 선택).
        """
        raise NotImplementedError
