"""
유니버스 종목 리스트 + 일봉 데이터를 수집해 data/cache 에 저장한다.

    python -m scripts.fetch_universe

증분 업데이트를 지원하므로, 매일 한 번 돌리면 최신 데이터가 누적된다.
"""
from src.config import load_config
from src.data import factors, fundamentals, universe
from src.utils.logger import get_logger

log = get_logger("fetch")


def main():
    cfg = load_config()
    u = cfg["universe"]
    bt = cfg["backtest"]

    log.info("1) 종목 리스트 수집: %s", u["markets"])
    tickers = universe.fetch_ticker_list(markets=tuple(u["markets"]))
    if tickers.empty:
        log.error("종목 리스트가 비었습니다. pykrx 설치/네트워크를 확인하세요.")
        return
    codes = tickers["code"].tolist()
    log.info("총 %d 종목. 일봉 수집을 시작합니다 (시간이 걸릴 수 있음).", len(codes))

    start = bt["start"].replace("-", "")
    universe.fetch_prices(codes, start=start, incremental=True)

    weights = cfg["strategy"].get("factor_weights", {})
    name = cfg["strategy"].get("name")

    if name == "korea_multifactor":
        log.info("2) 멀티팩터(가치/퀄리티/사이즈/수급) 패널 수집 — 시간이 걸립니다")
        factors.fetch_factor_panels(start=start, codes=codes)
    elif "quality" in weights:
        log.info("2) 퀄리티(펀더멘털) 패널 수집")
        fundamentals.fetch_quality_panel(start=start, codes=codes)

    log.info("완료. 이제 `python -m scripts.run_backtest` 로 성과를 검증하세요.")


if __name__ == "__main__":
    main()
