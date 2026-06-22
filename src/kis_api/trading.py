"""
국내주식 주문(현금 매수/매도) 및 잔고 조회.

모의/실전에 따라 TR_ID 가 다르므로 client.is_paper 로 분기한다.
  매수: 실전 TTTC0802U / 모의 VTTC0802U
  매도: 실전 TTTC0801U / 모의 VTTC0801U
  잔고: 실전 TTTC8434R / 모의 VTTC8434R
"""
from .client import KISClient
from ..utils.logger import get_logger

log = get_logger("kis.trading")


class Trading:
    def __init__(self, client: KISClient):
        self.c = client

    def _tr(self, real: str, paper: str) -> str:
        return paper if self.c.is_paper else real

    def order_cash(self, code: str, qty: int, price: int = 0,
                   side: str = "buy", order_type: str = "limit") -> dict:
        """
        현금 주문.

        side       : 'buy' | 'sell'
        order_type : 'limit'(지정가, price 필요) | 'market'(시장가, price=0)
        반환 dict 에 ok(bool), order_no, msg 포함.
        """
        path = "/uapi/domestic-stock/v1/trading/order-cash"
        if side == "buy":
            tr_id = self._tr("TTTC0802U", "VTTC0802U")
        elif side == "sell":
            tr_id = self._tr("TTTC0801U", "VTTC0801U")
        else:
            raise ValueError("side 는 buy|sell")

        if order_type == "market":
            ord_dvsn, ord_price = "01", "0"
        else:
            ord_dvsn, ord_price = "00", str(int(price))

        body = {
            "CANO": self.c.cano,
            "ACNT_PRDT_CD": self.c.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": ord_price,
        }
        out = self.c.post(path, tr_id, body, use_hashkey=True)
        ok = out.get("rt_cd") == "0"
        o = out.get("output", {}) or {}
        result = {
            "ok": ok,
            "order_no": o.get("ODNO", ""),
            "msg": out.get("msg1", ""),
            "raw": out,
        }
        lvl = log.info if ok else log.error
        lvl("[%s] %s %s x%d @%s -> %s",
            "모의" if self.c.is_paper else "실전",
            side.upper(), code, qty, ord_price, result["msg"])
        return result

    def buy(self, code: str, qty: int, price: int = 0, order_type: str = "limit") -> dict:
        return self.order_cash(code, qty, price, "buy", order_type)

    def sell(self, code: str, qty: int, price: int = 0, order_type: str = "limit") -> dict:
        return self.order_cash(code, qty, price, "sell", order_type)

    def balance(self) -> dict:
        """
        주식 잔고 조회 (실전 TTTC8434R / 모의 VTTC8434R).

        반환: {'cash': 예수금, 'total_eval': 총평가금액, 'positions': [...]}
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = self._tr("TTTC8434R", "VTTC8434R")
        params = {
            "CANO": self.c.cano,
            "ACNT_PRDT_CD": self.c.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        out = self.c.get(path, tr_id, params)
        positions = []
        for p in (out.get("output1", []) or []):
            qty = int(p.get("hldg_qty", 0) or 0)
            if qty <= 0:
                continue
            positions.append({
                "code": p.get("pdno", ""),
                "name": p.get("prdt_name", ""),
                "qty": qty,
                "avg_price": float(p.get("pchs_avg_pric", 0) or 0),
                "cur_price": int(p.get("prpr", 0) or 0),
                "eval_amount": int(p.get("evlu_amt", 0) or 0),
                "pnl_rate": float(p.get("evlu_pfls_rt", 0) or 0),
            })
        summary = (out.get("output2", []) or [{}])[0]
        return {
            "cash": int(summary.get("dnca_tot_amt", 0) or 0),
            "available_cash": int(summary.get("prvs_rcdl_excc_amt", 0) or 0),
            "total_eval": int(summary.get("tot_evlu_amt", 0) or 0),
            "positions": positions,
        }

    def daily_executions(self, start_date: str, end_date: str | None = None) -> list[dict]:
        """
        주식 일별 주문체결 조회 (실전 TTTC8001R / 모의 VTTC8001R).
        증권사에 남은 '실제 체결 내역'을 그대로 가져온다(= 공식 거래 기록).
        start_date/end_date: 'YYYYMMDD'. 3개월 이내 조회.
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        tr_id = self._tr("TTTC8001R", "VTTC8001R")
        end_date = end_date or start_date
        params = {
            "CANO": self.c.cano,
            "ACNT_PRDT_CD": self.c.acnt_prdt_cd,
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        out = self.c.get(path, tr_id, params)

        def _num(o, *keys):
            for k in keys:
                v = o.get(k)
                if v not in (None, "", "0"):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return 0.0

        execs = []
        for o in (out.get("output1", []) or []):
            filled = int(_num(o, "tot_ccld_qty", "ccld_qty"))
            if filled <= 0:
                continue
            execs.append({
                "date": o.get("ord_dt", ""),
                "time": o.get("ord_tmd", ""),
                "order_no": o.get("odno", ""),
                "code": o.get("pdno", ""),
                "name": o.get("prdt_name", ""),
                "side": o.get("sll_buy_dvsn_cd_name", o.get("sll_buy_dvsn_cd", "")),
                "order_qty": int(_num(o, "ord_qty")),
                "filled_qty": filled,
                "avg_price": _num(o, "avg_prvs", "ccld_prvs", "ord_unpr"),
                "amount": int(_num(o, "tot_ccld_amt", "ccld_amt")),
            })
        return execs
