"""価格データソースの組み立て。"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .base import (
    DEFAULT_HISTORY_DAYS,
    PriceSource,
    PriceSourceError,
    is_jp_code,
    normalize,
    quote_from_history,
)
from .cache import DEFAULT_CACHE_DIR, CachedSource
from .stooq import StooqSource
from .synthetic import SyntheticSource
from .yahoo import YahooSource

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HISTORY_DAYS",
    "PriceSource",
    "PriceSourceError",
    "FallbackSource",
    "build_source",
    "is_jp_code",
    "normalize",
    "quote_from_history",
]


class FallbackSource(PriceSource):
    """先頭のソースから順に試し、成功した最初の結果を返す。"""

    def __init__(self, sources: list[PriceSource]) -> None:
        if not sources:
            raise ValueError("ソースが 1 つも指定されていません")
        self.sources = sources
        self.name = "→".join(s.name for s in sources)

    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        errors: list[str] = []
        for source in self.sources:
            try:
                df = source.history(symbol, days)
                if not df.empty:
                    return df
                errors.append(f"{source.name}: 空データ")
            except Exception as exc:  # noqa: BLE001 — 次のソースへ倒すのが目的
                log.debug("%s で %s の取得に失敗: %s", source.name, symbol, exc)
                errors.append(f"{source.name}: {exc}")
        raise PriceSourceError(f"{symbol}: 全ソースで取得失敗 — " + " / ".join(errors))


def build_source(
    offline: bool = False,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    ttl_seconds: int = 3600,
    use_cache: bool = True,
) -> PriceSource:
    """既定の取得経路を組み立てる。

    offline=True のときはダミー価格を返す SyntheticSource になる。
    通常は Yahoo → Stooq のフォールバック構成で、それをキャッシュで包む。
    """
    if offline:
        return SyntheticSource()

    source: PriceSource = FallbackSource([YahooSource(), StooqSource()])
    if use_cache:
        source = CachedSource(source, cache_dir=cache_dir, ttl_seconds=ttl_seconds)
    return source
