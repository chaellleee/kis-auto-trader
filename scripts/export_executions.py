"""
오늘(또는 지정 구간) KIS 계좌의 '실제 체결 내역'을 CSV 로 내보낸다.
= 자동매매 시스템이 실제로 낸 주문의 공식 거래 기록 (제출용).

    python -m scripts.export_executions                 # 오늘
    python -m scripts.export_executions 20260622        # 특정일
    python -m scripts.export_executions 20260601 20260622  # 구간

결과: results/executions_<날짜>.csv  +  콘솔 요약
"""
import csv
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from src.kis_api import KISClient, Trading
from src.utils.logger import get_logger

log = get_logger("export_exec")


def main():
    args = sys.argv[1:]
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    start = args[0] if len(args) >= 1 else today
    end = args[1] if len(args) >= 2 else start

    client = KISClient()
    trade = Trading(client)
    mode_kr = "모의" if client.is_paper else "실전"

    execs = trade.daily_executions(start, end)
    os.makedirs("results", exist_ok=True)
    out_path = os.path.join("results", f"executions_{start}_{end}.csv"
                            if start != end else f"executions_{start}.csv")

    cols = ["date", "time", "order_no", "code", "name", "side",
            "order_qty", "filled_qty", "avg_price", "amount"]
    headers = ["체결일", "시각", "주문번호", "종목코드", "종목명", "구분",
               "주문수량", "체결수량", "체결평균가", "체결금액"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for e in execs:
            w.writerow([e[c] for c in cols])

    buys = [e for e in execs if "매수" in e["side"]]
    sells = [e for e in execs if "매도" in e["side"]]
    buy_amt = sum(e["amount"] for e in buys)
    sell_amt = sum(e["amount"] for e in sells)
    print("\n" + "=" * 60)
    print(f"  실제 체결 내역 ({mode_kr})  {start}~{end}")
    print("=" * 60)
    print(f"  총 체결: {len(execs)}건  (매수 {len(buys)} / 매도 {len(sells)})")
    print(f"  매수금액 합계: {buy_amt:,}원")
    print(f"  매도금액 합계: {sell_amt:,}원")
    print(f"  저장: {out_path}")
    print("=" * 60)
    if not execs:
        print("  (체결 내역 없음 — 장중 체결 후 다시 실행하세요)")


if __name__ == "__main__":
    main()
