"""
모의/실전 자동매매 1회 실행 (리밸런싱).

    python -m scripts.run_paper_trading            # .env 의 KIS_MODE 사용
    python -m scripts.run_paper_trading --dry-run  # 주문 전송 없이 계획만 출력

장중 내내 자동으로 돌리려면 scripts.run_live 를 사용하세요.
⚠️ 실전(KIS_MODE=real)에서는 실제 주문이 나갑니다. --dry-run 으로 먼저 확인하세요.
"""
import argparse

from src.trader import TradingContext, rebalance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="주문 전송 없이 계획만 출력")
    args = parser.parse_args()

    ctx = TradingContext()
    rebalance(ctx, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
