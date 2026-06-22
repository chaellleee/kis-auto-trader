"""
자동매매 실행 로직 (모의/실전 공용).

1회 실행 스크립트(run_paper_trading)와 장중 루프(run_live)가 이 모듈을 공유한다.
  - TradingContext : 클라이언트/전략/매니저 묶음 (한 번 생성해 재사용)
  - risk_check()   : 보유종목 손절/익절만 점검·실행 (장중 자주 호출)
  - rebalance()    : 전체 리밸런싱 (하루 1회)
  - market_phase() : 현재가 장중인지 판단 (네트워크 불필요, 테스트 가능)
"""
import csv
import os
from datetime import datetime, time as dtime

import pandas as pd

from .config import build_strategy, load_config
from .data import universe
from .kis_api import KISClient, Quotations, Trading
from .portfolio.manager import PortfolioManager
from .utils.logger import get_logger

log = get_logger("trader")

TRADE_LOG = os.path.join("results", "trade_log.csv")


def _record_trade(ctx, side: str, code: str, qty: int, price: int,
                  result: dict | None, dry: bool) -> None:
    """주문 1건을 results/trade_log.csv 에 누적 기록한다."""
    os.makedirs("results", exist_ok=True)
    is_new = not os.path.exists(TRADE_LOG)
    r = result or {}
    status = "DRY(계획)" if dry else ("성공" if r.get("ok") else "실패/거부")
    with open(TRADE_LOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["시각", "모드", "구분", "종목코드", "수량",
                        "가격", "주문번호", "상태", "메시지"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "모의" if ctx.is_paper else "실전",
            side, code, qty, price,
            r.get("order_no", ""), status, r.get("msg", ""),
        ])


class TradingContext:
    """매 호출마다 객체를 다시 만들지 않도록 묶어둔다."""

    def __init__(self):
        self.cfg = load_config()
        self.client = KISClient()
        self.quotes = Quotations(self.client)
        self.trade = Trading(self.client)
        self.strat = build_strategy(self.cfg)
        self.mgr = PortfolioManager(self.cfg)

    @property
    def is_paper(self) -> bool:
        return self.client.is_paper

    @property
    def mode_kr(self) -> str:
        return "모의투자" if self.is_paper else "🔴 실전투자"


def _round_to_tick(price: int) -> int:
    """한국거래소 호가단위로 가격 보정."""
    p = int(price)
    if p < 2000: tick = 1
    elif p < 5000: tick = 5
    elif p < 20000: tick = 10
    elif p < 50000: tick = 50
    elif p < 200000: tick = 100
    elif p < 500000: tick = 500
    else: tick = 1000
    return (p // tick) * tick


def _do_sell(ctx: TradingContext, code: str, qty: int, dry: bool) -> None:
    execu = ctx.cfg["execution"]
    if dry:
        log.info("[DRY] 매도 계획 %s x%d", code, qty)
        _record_trade(ctx, "매도", code, qty, 0, None, True)
        return
    try:
        price = ctx.quotes.current_price(code)["price"]
    except Exception:
        price = 0
    otype = execu["order_type"]
    if otype == "limit" and price > 0:
        px = _round_to_tick(int(price * (1 - execu["limit_slippage"])))
        res = ctx.trade.sell(code, qty, px, "limit")
    else:
        px = 0
        res = ctx.trade.sell(code, qty, 0, "market")
    _record_trade(ctx, "매도", code, qty, px, res, False)


def risk_check(ctx: TradingContext, dry_run: bool = False) -> int:
    """보유종목 손절/익절만 점검·실행. 트리거된 매도 건수 반환 (장중 자주 호출용)."""
    risk = ctx.cfg["risk"]
    bal = ctx.trade.balance()
    held = {p["code"]: p for p in bal["positions"]}
    n = 0
    for code, pos in held.items():
        rate = pos["pnl_rate"] / 100.0
        if rate <= risk["stop_loss"]:
            log.info("손절 트리거 %s (%.2f%%)", code, pos["pnl_rate"])
            _do_sell(ctx, code, pos["qty"], dry_run)
            n += 1
        elif rate >= risk["take_profit"]:
            half = max(1, pos["qty"] // 2)
            log.info("익절(절반) %s (%.2f%%)", code, pos["pnl_rate"])
            _do_sell(ctx, code, half, dry_run)
            n += 1
    if n == 0:
        log.info("리스크 점검: 손절/익절 트리거 없음 (보유 %d종목)", len(held))
    return n


def rebalance(ctx: TradingContext, dry_run: bool = False) -> int:
    """전체 리밸런싱(손절/익절 + 목표비중 매매). 주문 건수 반환 (하루 1회)."""
    execu = ctx.cfg["execution"]
    log.info("===== 리밸런싱 (%s)%s =====",
             ctx.mode_kr, " [DRY-RUN]" if dry_run else "")

    bal = ctx.trade.balance()
    total_asset = bal["total_eval"] or bal["cash"]
    log.info("총자산 %s원 / 가용현금 %s원 / 보유 %d종목",
             f"{total_asset:,}", f"{bal['available_cash']:,}", len(bal["positions"]))
    held = {p["code"]: p for p in bal["positions"]}
    cur_weights = pd.Series(
        {c: p["eval_amount"] / total_asset for c, p in held.items()}
    ) if total_asset > 0 else pd.Series(dtype=float)

    close = universe.load_price_panel()
    as_of = close.index[-1]
    benchmark = close.mean(axis=1)
    cand = close
    u = ctx.cfg.get("universe", {})
    top_n = int(u.get("top_n_liquid", 0) or 0)
    if top_n > 0:
        value_panel = universe.load_value_panel()
        liq = universe.liquid_as_of(value_panel, as_of, top_n,
                                    float(u.get("min_avg_value", 0) or 0))
        cols = [c for c in liq if c in close.columns]
        if cols:
            cand = close[cols]
    target_w = ctx.strat.generate_signals(cand, as_of, current_holding=set(held.keys()))
    target_w = ctx.mgr.size_positions(target_w, close, as_of, benchmark)
    target_w = ctx.mgr.apply_turnover_limit(cur_weights, target_w)
    if target_w.empty:
        log.info("매수 신호 없음(약세장/조건 미충족). 신규 매수 건너뜀.")

    for code, pos in list(held.items()):
        rate = pos["pnl_rate"] / 100.0
        if rate <= ctx.cfg["risk"]["stop_loss"]:
            log.info("손절 트리거 %s (%.2f%%)", code, pos["pnl_rate"])
            _do_sell(ctx, code, pos["qty"], dry_run)
            held.pop(code, None)
        elif rate >= ctx.cfg["risk"]["take_profit"]:
            log.info("익절(절반) %s (%.2f%%)", code, pos["pnl_rate"])
            _do_sell(ctx, code, max(1, pos["qty"] // 2), dry_run)

    target_codes = set(target_w.index)
    for code, pos in list(held.items()):
        if code not in target_codes:
            log.info("리밸런싱 매도(목표 이탈) %s", code)
            _do_sell(ctx, code, pos["qty"], dry_run)

    n_orders = 0
    for code, w in target_w.sort_values(ascending=False).items():
        if n_orders >= execu["max_order_per_run"]:
            log.info("1회 최대 주문 수 도달, 나머지는 다음 실행으로.")
            break
        budget = total_asset * float(w)
        cur = held.get(code)
        try:
            price = ctx.quotes.current_price(code)["price"]
        except Exception as e:
            log.warning("%s 현재가 조회 실패: %s", code, e)
            continue
        if price <= 0:
            continue
        target_qty = int(budget // price)
        have_qty = cur["qty"] if cur else 0
        buy_qty = target_qty - have_qty
        if buy_qty < 0:
            _do_sell(ctx, code, -buy_qty, dry_run)
            continue
        if buy_qty == 0:
            continue
        limit = _round_to_tick(int(price * (1 + execu["limit_slippage"])))
        if dry_run:
            log.info("[DRY] 매수 계획 %s x%d @%s (목표비중 %.1f%%)",
                     code, buy_qty, f"{limit:,}", w * 100)
            _record_trade(ctx, "매수", code, buy_qty, limit, None, True)
        else:
            otype = execu["order_type"]
            px = limit if otype == "limit" else 0
            res = ctx.trade.buy(code, buy_qty, px, otype)
            _record_trade(ctx, "매수", code, buy_qty, px or price, res, False)
        n_orders += 1

    log.info("===== 리밸런싱 완료 (주문 %d건) =====", n_orders)
    return n_orders


def _parse_hhmm(s: str) -> dtime:
    h, m = str(s).split(":")
    return dtime(int(h), int(m))


def market_phase(now: datetime, cfg: dict) -> str:
    """
    현재 시각의 장 상태를 반환 (네트워크 불필요).
      'weekend'     주말
      'before_open' 개장 전
      'after_close' 폐장 후
      'open'        장중
    공휴일은 자동 감지하지 않는다(주문이 무의미하게 나가지 않도록 장중에도
    데이터 부재 시 전략이 신규매수를 스킵함).
    """
    live = cfg.get("live", {})
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    if t < _parse_hhmm(live.get("market_open", "09:00")):
        return "before_open"
    if t > _parse_hhmm(live.get("market_close", "15:20")):
        return "after_close"
    return "open"
