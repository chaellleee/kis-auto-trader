"""
GitHub 공개용 결과물 생성 — 계좌번호 등 민감정보를 제거한 사본을 docs/ 에 만든다.

    python -m scripts.export_public_results

원본 results/ 는 계속 비공개(.gitignore)로 두고, docs/ 에 안전한 사본만 만들어
공개 레포에 올린다. 백테스트 차트·지표와 거래 기록(계좌번호 마스킹)을 포함한다.
"""
import csv
import glob
import os
import shutil

RESULTS = "results"
DOCS = "docs"


def _copy_if_exists(src, dst):
    if os.path.exists(src):
        shutil.copy(src, dst)
        print("복사:", dst)


def _export_trades():
    """trade_log.csv 에서 실거래(연습 제외)만 추려 저장. (계좌번호 컬럼 없음)"""
    src = os.path.join(RESULTS, "trade_log.csv")
    if not os.path.exists(src):
        return
    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header, body = rows[0], rows[1:]
    real = [r for r in body if len(r) >= 8 and "DRY" not in r[7]]
    dst = os.path.join(DOCS, "trades.csv")
    with open(dst, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(real)
    print(f"거래기록(실거래 {len(real)}건, 연습 제외): {dst}")


def _export_holdings():
    """holdings_*.csv 의 '계좌' 컬럼을 마스킹해 저장."""
    files = sorted(glob.glob(os.path.join(RESULTS, "holdings_*.csv")))
    if not files:
        return
    src = files[-1]
    with open(src, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header = rows[0]
    acct_idx = header.index("계좌") if "계좌" in header else None
    for r in rows[1:]:
        if acct_idx is not None and len(r) > acct_idx and r[acct_idx]:
            r[acct_idx] = "(비공개)"
    dst = os.path.join(DOCS, "holdings.csv")
    with open(dst, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    print(f"보유내역(계좌 마스킹): {dst}")


def main():
    os.makedirs(DOCS, exist_ok=True)
    _copy_if_exists(os.path.join(RESULTS, "equity_curve.png"),
                    os.path.join(DOCS, "backtest_equity_curve.png"))
    _copy_if_exists(os.path.join(RESULTS, "metrics.csv"),
                    os.path.join(DOCS, "backtest_metrics.csv"))
    _export_trades()
    _export_holdings()
    print("\n완료 — docs/ 의 파일만 GitHub 에 올라갑니다 (계좌번호 제거됨).")


if __name__ == "__main__":
    main()
