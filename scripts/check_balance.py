"""
현재 계좌(모의/실전) 잔고·보유종목을 출력하고 CSV 로 저장한다.

    python -m scripts.check_balance

보유종목 + 평균매수가 = '자동매매가 무엇을 얼마에 샀는지'의 기록이므로,
results/holdings_<날짜>.csv 가 제출용 거래/보유 기록이 된다.
"""
import csv
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from src.kis_api import KISClient, Trading
from src.utils.logger import get_logger

log = get_logger("check_balance")


def main():
    client = KISClient()
    trade = Trading(client)
    mode_kr = "모의투자" if client.is_paper else "🔴 실전투자"

    bal = trade.balance()
    print("\n" + "=" * 64)
    print(f"  계좌 잔고  ({mode_kr})   계좌: {client.cano}-{client.acnt_prdt_cd}")
    print("=" * 64)
    print(f"  예수금(현금)   : {bal['cash']:>15,} 원")
    print(f"  가용현금       : {bal['available_cash']:>15,} 원")
    print(f"  총평가금액     : {bal['total_eval']:>15,} 원")
    print("-" * 64)

    pos = bal["positions"]
    if not pos:
        print("  보유 종목 없음 (아직 체결된 매수가 없거나 전량 매도 상태)")
    else:
        print(f"  보유 종목 {len(pos)}개")
        print(f"  {'종목명':<12}{'수량':>8}{'평균가':>11}{'현재가':>11}{'평가손익률':>11}")
        print("  " + "-" * 60)
        for p in pos:
            name = (p["name"] or p["code"])[:12]
            print(f"  {name:<12}{p['qty']:>8,}{int(p['avg_price']):>11,}"
                  f"{p['cur_price']:>11,}{p['pnl_rate']:>10.2f}%")
    print("=" * 64 + "\n")

    os.makedirs("results", exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    path = os.path.join("results", f"holdings_{today}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["조회시각", "모드", "계좌", "종목코드", "종목명", "보유수량",
                    "평균매수가", "현재가", "평가금액", "평가손익률(%)"])
        ts = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
        acct = f"{client.cano}-{client.acnt_prdt_cd}"
        md = "모의" if client.is_paper else "실전"
        for p in pos:
            w.writerow([ts, md, acct, p["code"], p["name"], p["qty"],
                        int(p["avg_price"]), p["cur_price"],
                        p["eval_amount"], p["pnl_rate"]])
        w.writerow([])
        w.writerow([ts, md, acct, "요약", f"보유 {len(pos)}종목",
                    "", "", "", bal["total_eval"], ""])
    log.info("보유내역 저장: %s (%d종목)", path, len(pos))
    print(f"  → 거래/보유 기록 저장: {path}\n")


if __name__ == "__main__":
    main()
