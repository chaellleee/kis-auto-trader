"""
국내주식 시세 조회.

현재가, 일/주/월봉(기간별 시세)을 조회한다.
시세 조회 TR_ID 는 모의/실전이 동일하다 (F 로 시작).
"""
import pandas as pd

from .client import KISClient


class Quotations:
    def __init__(self, client: KISClient):
        self.c = client

    def current_price(self, code: str) -> dict:
        """현재가 시세 (FHKST01010100)."""
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
        out = self.c.get(path, "FHKST01010100", params)
        o = out.get("output", {})
        return {
            "code": code,
            "price": int(o.get("stck_prpr", 0) or 0),
            "change_rate": float(o.get("prdy_ctrt", 0) or 0),
            "volume": int(o.get("acml_vol", 0) or 0),
            "value": int(o.get("acml_tr_pbmn", 0) or 0),
            "market_cap": int(o.get("hts_avls", 0) or 0),
            "high52": int(o.get("w52_hgpr", 0) or 0),
            "low52": int(o.get("w52_lwpr", 0) or 0),
        }

    def daily_chart(self, code: str, start: str, end: str,
                    period: str = "D", adjust: bool = True) -> pd.DataFrame:
        """
        기간별 일/주/월봉 (FHKST03010100).

        Parameters
        ----------
        start, end : 'YYYYMMDD'
        period     : 'D'(일) | 'W'(주) | 'M'(월)
        adjust     : 수정주가 여부
        한 번에 최대 약 100건 반환되므로, 긴 구간은 end 를 당겨가며 페이징한다.
        """
        path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        rows = []
        cur_end = end
        for _ in range(60):
            params = {
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": code,
                "fid_input_date_1": start,
                "fid_input_date_2": cur_end,
                "fid_period_div_code": period,
                "fid_org_adj_prc": "0" if adjust else "1",
            }
            out = self.c.get(path, "FHKST03010100", params)
            chunk = out.get("output2", []) or []
            chunk = [r for r in chunk if r.get("stck_bsop_date")]
            if not chunk:
                break
            rows.extend(chunk)

            oldest = min(r["stck_bsop_date"] for r in chunk)
            if oldest <= start:
                break
            cur_end = (pd.to_datetime(oldest) - pd.Timedelta(days=1)).strftime("%Y%m%d")

        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "stck_bsop_date": "date", "stck_oprc": "open", "stck_hgpr": "high",
            "stck_lwpr": "low", "stck_clpr": "close", "acml_vol": "volume",
        })
        keep = ["date", "open", "high", "low", "close", "volume"]
        df = df[[c for c in keep if c in df.columns]].copy()
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).drop_duplicates("date")
        df = df[df["date"] >= pd.to_datetime(start)]
        return df.sort_values("date").reset_index(drop=True)
