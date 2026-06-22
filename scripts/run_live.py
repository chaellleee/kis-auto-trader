"""
장중 자동매매 데몬 (계속 떠 있으면서 자동 실행).

    python -m scripts.run_live                 # 실제 주문(모의/실전은 KIS_MODE)
    python -m scripts.run_live --dry-run       # 주문 없이 동작만 로그
    python -m scripts.run_live --once          # 한 사이클만 돌고 종료(테스트)

동작 (config.yaml 의 live: 설정)
--------------------------------
- 주말/장 시작 전/마감 후에는 대기(sleep)한다.
- 장중:
    * rebalance_at(예 09:05)에 하루 1회 전체 리밸런싱.
    * 그 외에는 risk_check_interval_min(예 5분)마다 손절/익절만 점검.
- Ctrl+C 로 안전하게 종료.

※ 이 전략은 일봉 기반이라 '하루 1회 리밸런싱 + 장중 손절/익절 감시' 가 합리적입니다.
   더 잦은 매매는 비용만 키우므로 의도적으로 제한합니다.
※ 컴퓨터가 켜져 있고 절전(슬립)되지 않아야 계속 돕니다(맥: caffeinate 권장).
   터미널을 닫아도 돌게 하려면 README 의 launchd/cron 방법을 참고하세요.
"""
import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from src.data import universe
from src.trader import TradingContext, market_phase, rebalance, risk_check
from src.utils.logger import get_logger

log = get_logger("run_live")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="주문 없이 동작만 로그")
    parser.add_argument("--once", action="store_true", help="한 사이클만 실행 후 종료")
    parser.add_argument("--monitor-only", action="store_true",
                        help="리밸런싱(재매매) 없이 손절/익절 감시만 (이미 매수 완료한 경우)")
    args = parser.parse_args()

    ctx = TradingContext()
    live = ctx.cfg.get("live", {})
    tz = ZoneInfo(live.get("timezone", "Asia/Seoul"))
    interval = int(live.get("risk_check_interval_min", 5)) * 60
    rebal_at = live.get("rebalance_at", "09:05")
    update_data = bool(live.get("update_data_before_rebalance", False))

    mode_extra = " [DRY-RUN]" if args.dry_run else ""
    mode_extra += " [감시전용]" if args.monitor_only else ""
    log.info("장중 자동매매 시작 (%s)%s | 점검주기 %d분, 리밸런싱 %s",
             ctx.mode_kr, mode_extra, interval // 60,
             "비활성(감시전용)" if args.monitor_only else rebal_at)

    last_rebalance_date = None
    rh, rm = (int(x) for x in str(rebal_at).split(":"))

    try:
        while True:
            now = datetime.now(tz)
            phase = market_phase(now, ctx.cfg)

            if phase != "open":
                msg = {"weekend": "주말", "before_open": "장 시작 전",
                       "after_close": "장 마감"}[phase]
                log.info("%s — 대기 중 (%s)", msg, now.strftime("%m/%d %H:%M"))
                if args.once:
                    break
                time.sleep(60 if phase == "before_open" else 300)
                continue

            today = now.date()
            past_rebal_time = (now.hour, now.minute) >= (rh, rm)

            do_rebal = (not args.monitor_only
                        and last_rebalance_date != today and past_rebal_time)
            if do_rebal:
                if update_data:
                    log.info("리밸런싱 전 일봉 데이터 증분 갱신...")
                    _update_data(ctx)
                try:
                    rebalance(ctx, dry_run=args.dry_run)
                    last_rebalance_date = today
                except Exception as e:
                    log.error("리밸런싱 중 오류(다음 주기 재시도): %s", e)
            else:
                try:
                    risk_check(ctx, dry_run=args.dry_run)
                except Exception as e:
                    log.error("리스크 점검 중 오류: %s", e)

            if args.once:
                break
            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("사용자 종료(Ctrl+C). 안전하게 종료합니다.")


def _update_data(ctx):
    try:
        u = ctx.cfg["universe"]
        bt = ctx.cfg["backtest"]
        tickers = universe.fetch_ticker_list(markets=tuple(u["markets"]))
        if not tickers.empty:
            universe.fetch_prices(tickers["code"].tolist(),
                                  start=bt["start"].replace("-", ""), incremental=True)
    except Exception as e:
        log.warning("데이터 갱신 실패(기존 캐시로 진행): %s", e)


if __name__ == "__main__":
    main()
