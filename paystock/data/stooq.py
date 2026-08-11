"""Stooq の CSV から日足を取る (Yahoo が落ちたときのフォールバック)。

API キー不要。日本株は `<コード>.jp`、米国株は `<ティッカー>.us`。
出来高が 0 で返ることがあるので、出来高依存の指標は None になりうる。
"""

from __future__ import annotations

import io

import pandas as pd

from .base import DEFAULT_HISTORY_DAYS, PriceSource, PriceSourceError, is_jp_code, normalize

CSV_URL = "https://stooq.com/q/d/l/"


def to_stooq_symbol(symbol: str) -> str:
    symbol = symbol.strip().lower()
    if is_jp_code(symbol):
        return f"{symbol}.jp"
    if "." not in symbol:
        return f"{symbol}.us"
    return symbol


class StooqSource(PriceSource):
    name = "stooq"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        import requests  # noqa: PLC0415

        ssym = to_stooq_symbol(symbol)
        try:
            res = requests.get(
                CSV_URL, params={"s": ssym, "i": "d"}, timeout=self.timeout
            )
            res.raise_for_status()
            text = res.text
        except Exception as exc:  # noqa: BLE001
            raise PriceSourceError(f"{ssym}: Stooq への接続に失敗 ({exc})") from exc

        # 銘柄が無いと 200 で "No data" という本文が返ってくる
        if "Date" not in text.splitlines()[0]:
            raise PriceSourceError(f"{ssym}: Stooq にデータがありません")

        df = pd.read_csv(io.StringIO(text), parse_dates=["Date"], index_col="Date")
        if df.empty:
            raise PriceSourceError(f"{ssym}: Stooq が空の CSV を返しました")
        return normalize(df).tail(days)
