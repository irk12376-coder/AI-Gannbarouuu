"""日足のローカルキャッシュ。

同じ銘柄を screen / report / backtest で何度も引くので、TTL 付きで CSV に落とす。
Yahoo の非公式 API を叩く回数を減らす目的もある (レート制限対策)。
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from .base import DEFAULT_HISTORY_DAYS, PriceSource, normalize

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "paystock"


class CachedSource(PriceSource):
    """任意の PriceSource をラップして、日足をディスクにキャッシュする。"""

    def __init__(
        self,
        inner: PriceSource,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        ttl_seconds: int = 3600,
    ) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.name = f"{inner.name}+cache"

    def _path(self, symbol: str, days: int) -> Path:
        safe = symbol.replace("/", "_").replace("\\", "_")
        return self.cache_dir / self.inner.name / f"{safe}_{days}.csv"

    def _fresh(self, path: Path) -> bool:
        return path.exists() and (time.time() - path.stat().st_mtime) < self.ttl_seconds

    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        path = self._path(symbol, days)
        if self._fresh(path):
            try:
                return normalize(pd.read_csv(path, index_col=0, parse_dates=[0]))
            except Exception:  # noqa: BLE001 — 壊れたキャッシュは黙って捨てて取り直す
                path.unlink(missing_ok=True)

        df = self.inner.history(symbol, days)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path)
        return df

    def clear(self) -> int:
        """キャッシュを全消しして、消したファイル数を返す。"""
        root = self.cache_dir / self.inner.name
        if not root.exists():
            return 0
        files = list(root.glob("*.csv"))
        for f in files:
            f.unlink(missing_ok=True)
        return len(files)
