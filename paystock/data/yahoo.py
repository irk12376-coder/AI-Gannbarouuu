"""Yahoo Finance から日足を取る。

yfinance が入っていればそれを使い、無ければ chart API を直接叩く。
どちらの経路でも同じ形の DataFrame を返す。

注意: Yahoo Finance は非公式 API であり、レート制限や仕様変更で落ちることがある。
落ちたときは data/stooq.py にフォールバックする (sources.build() が面倒を見る)。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .base import DEFAULT_HISTORY_DAYS, PriceSource, PriceSourceError, is_jp_code, normalize

CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
_USER_AGENT = "Mozilla/5.0 (compatible; paystock/1.0)"


def to_yahoo_symbol(symbol: str) -> str:
    """証券コードを Yahoo のシンボルに変換する。5401 -> 5401.T"""
    symbol = symbol.strip().upper()
    if is_jp_code(symbol):
        return f"{symbol}.T"
    return symbol


class YahooSource(PriceSource):
    name = "yahoo"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        ysym = to_yahoo_symbol(symbol)
        try:
            return self._via_yfinance(ysym, days)
        except ImportError:
            return self._via_http(ysym, days)

    def _via_yfinance(self, ysym: str, days: int) -> pd.DataFrame:
        import yfinance  # noqa: PLC0415 — 任意依存なので関数内 import

        # 営業日でなくカレンダー日で指定する必要があるので、余裕を持って 1.6 倍取る
        period = f"{max(int(days * 1.6), 30)}d"
        df = yfinance.Ticker(ysym).history(period=period, auto_adjust=False)
        if df is None or df.empty:
            raise PriceSourceError(f"{ysym}: yfinance が空の結果を返しました")
        return normalize(df).tail(days)

    def _via_http(self, ysym: str, days: int) -> pd.DataFrame:
        import requests  # noqa: PLC0415

        end = int(dt.datetime.now(dt.timezone.utc).timestamp())
        start = end - int(days * 1.6) * 86400
        try:
            res = requests.get(
                CHART_URL.format(symbol=ysym),
                params={"period1": start, "period2": end, "interval": "1d"},
                headers={"User-Agent": _USER_AGENT},
                timeout=self.timeout,
            )
            res.raise_for_status()
            payload = res.json()
        except Exception as exc:  # noqa: BLE001 — 通信/JSON 失敗をまとめて包む
            raise PriceSourceError(f"{ysym}: Yahoo chart API に失敗 ({exc})") from exc

        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            raise PriceSourceError(f"{ysym}: Yahoo chart API が結果を返しませんでした")

        node = result[0]
        quote = node["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "open": quote.get("open"),
                "high": quote.get("high"),
                "low": quote.get("low"),
                "close": quote.get("close"),
                "volume": quote.get("volume"),
            },
            index=pd.to_datetime(node["timestamp"], unit="s"),
        )
        return normalize(df).tail(days)
