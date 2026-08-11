"""スクリーニング対象銘柄リストの読み込み。

data/universe_jp.csv は「東証の主要銘柄」を手で並べたもの。
PayPay 証券の日本株は取扱銘柄が限られるので、実際に買えるかは
アプリ側で確認すること (このリストは取扱の保証ではない)。

自分のリストを使いたい場合は code,name,sector の 3 列の CSV を用意して
`--universe path/to/your.csv` で渡す。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_UNIVERSE = Path("data/universe_jp.csv")
REQUIRED_COLUMNS = {"code", "name", "sector"}


@dataclass(frozen=True)
class UniverseEntry:
    code: str
    name: str
    sector: str


def load(path: Path | str = DEFAULT_UNIVERSE) -> list[UniverseEntry]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"銘柄リストが見つかりません: {path}")

    df = pd.read_csv(path, dtype=str).fillna("")
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{path}: 列が不足しています {sorted(missing)}")

    return [
        UniverseEntry(
            code=str(row["code"]).strip(),
            name=str(row["name"]).strip(),
            sector=str(row["sector"]).strip() or "その他",
        )
        for _, row in df.iterrows()
        if str(row["code"]).strip()
    ]
