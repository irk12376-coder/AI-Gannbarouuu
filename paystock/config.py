"""設定ファイル (portfolio.yaml) の読み書き。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .analysis.rules import RuleConfig
from .models import AssetKind, Holding

DEFAULT_PORTFOLIO_PATH = Path("portfolio.yaml")


class ConfigError(ValueError):
    """設定ファイルの内容が不正なときに投げる。"""


@dataclass
class TradeSettings:
    """自動売買エンジンの安全装置。既定値は「まず何も起きない」側に倒してある。"""

    enabled: bool = False              # 明示的に true にしない限り発注しない
    broker: str = "paper"              # paper | kabus
    dry_run: bool = True               # true なら発注せずログのみ
    max_position_pct: float = 20.0     # 1 銘柄あたりの上限 (総資産比 %)
    max_order_amount: float = 30000.0  # 1 発注あたりの上限金額 (円)
    max_orders_per_day: int = 5
    max_daily_loss_pct: float = 5.0    # 当日の評価損がこれを超えたら全停止
    min_cash_buffer: float = 1000.0    # これ未満の現金は買いに使わない
    kill_switch_file: str = "KILL"     # このファイルがあれば起動しない
    trading_hours_only: bool = True    # 東証の立会時間内のみ動かす


@dataclass
class AppConfig:
    holdings: list[Holding] = field(default_factory=list)
    rules: RuleConfig = field(default_factory=RuleConfig)
    trade: TradeSettings = field(default_factory=TradeSettings)
    universe_path: str = "data/universe_jp.csv"
    path: Path | None = None

    @property
    def total_value(self) -> float:
        """スクリーンショット時点の総資産 (現金含む)。"""
        return sum(h.market_value for h in self.holdings)

    @property
    def cash(self) -> float:
        return sum(h.market_value for h in self.holdings if h.kind is AssetKind.CASH)

    def priceable(self) -> list[Holding]:
        return [h for h in self.holdings if h.priceable and h.data_symbol]


def _as_float(value: Any, field_name: str, holding_name: str) -> float:
    """"57,981" のようなカンマ区切り文字列も受け付ける。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("円", "").strip())
    except ValueError as exc:
        raise ConfigError(
            f"{holding_name}: {field_name} を数値として読めません ({value!r})"
        ) from exc


def _parse_holding(raw: dict[str, Any], index: int) -> Holding:
    name = str(raw.get("name") or f"(名称未設定 #{index + 1})")

    kind_raw = str(raw.get("kind", "jp_stock")).strip()
    try:
        kind = AssetKind(kind_raw)
    except ValueError as exc:
        valid = ", ".join(k.value for k in AssetKind)
        raise ConfigError(
            f"{name}: kind が不正です ({kind_raw!r})。使える値: {valid}"
        ) from exc

    symbol = raw.get("symbol")
    if symbol is not None:
        symbol = str(symbol).strip()

    holding = Holding(
        name=name,
        kind=kind,
        symbol=symbol or None,
        shares=_as_float(raw.get("shares"), "shares", name),
        market_value=_as_float(raw.get("market_value"), "market_value", name),
        pnl=_as_float(raw.get("pnl"), "pnl", name),
        proxy_symbol=(str(raw["proxy_symbol"]).strip() if raw.get("proxy_symbol") else None),
        sector=str(raw.get("sector") or "その他"),
        note=str(raw.get("note") or ""),
    )

    if kind in (AssetKind.JP_STOCK, AssetKind.US_STOCK) and not holding.symbol:
        raise ConfigError(f"{name}: 上場銘柄には symbol (銘柄コード) が必要です")
    if kind is AssetKind.FUND and not holding.proxy_symbol:
        raise ConfigError(
            f"{name}: 投資信託には proxy_symbol (連動指数のシンボル) が必要です"
        )
    return holding


def _parse_dataclass(cls, raw: dict[str, Any] | None, label: str):
    """既知のフィールドだけを拾って dataclass を作る。未知キーは例外にする。"""
    if not raw:
        return cls()
    known = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(
            f"{label} に未知の項目があります: {sorted(unknown)}。"
            f" 使える項目: {sorted(known)}"
        )
    return cls(**raw)


def load(path: Path | str = DEFAULT_PORTFOLIO_PATH) -> AppConfig:
    """portfolio.yaml を読み込む。"""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"{path} が見つかりません。`paystock init` で雛形を作成してください。"
        )

    with path.open(encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: トップレベルがマッピングではありません")

    holdings_raw = raw.get("holdings") or []
    if not isinstance(holdings_raw, list):
        raise ConfigError(f"{path}: holdings はリストで書いてください")

    holdings = [_parse_holding(h, i) for i, h in enumerate(holdings_raw)]

    return AppConfig(
        holdings=holdings,
        rules=_parse_dataclass(RuleConfig, raw.get("rules"), "rules"),
        trade=_parse_dataclass(TradeSettings, raw.get("trade"), "trade"),
        universe_path=str(raw.get("universe_path") or "data/universe_jp.csv"),
        path=path,
    )
