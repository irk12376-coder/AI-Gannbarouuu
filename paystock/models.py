"""共通データモデル。

このモジュールは外部依存を持たない(pandas も import しない)。
テストや型定義から軽量に import できることを優先している。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class AssetKind(str, Enum):
    """保有資産の種別。値の取得方法がこれで変わる。"""

    JP_STOCK = "jp_stock"          # 国内上場株 (例: 5401)
    US_STOCK = "us_stock"          # 米国上場株 (例: AAPL)
    FUND = "fund"                  # 投資信託。連動指数をプロキシとして評価する
    PRIVATE = "private"            # 未上場 (例: SpaceX)。市場価格が存在しない
    CASH = "cash"                  # 現金


class Action(str, Enum):
    """診断結果として提示するアクション。"""

    BUY_MORE = "買い増し検討"
    HOLD = "保有継続"
    TRIM = "一部利確"
    SELL = "売却検討"
    STOP_LOSS = "損切り検討"
    NO_SIGNAL = "判定不可"


@dataclass(frozen=True)
class Holding:
    """portfolio.yaml の 1 行に対応する保有銘柄。

    PayPay 証券は金額指定売買なので株数が小数になる。株数・評価額・損益額の
    3 つが分かれば取得原価が復元できるので、その 3 つを正とする。
    """

    name: str
    kind: AssetKind
    symbol: Optional[str] = None       # 銘柄コード。CASH / PRIVATE では None 可
    shares: float = 0.0
    market_value: float = 0.0          # スクリーンショット時点の評価額 (円)
    pnl: float = 0.0                   # 同 評価損益 (円)
    proxy_symbol: Optional[str] = None  # FUND の連動指数など、代替で見るシンボル
    sector: str = "その他"              # 集中度チェックに使う分類
    note: str = ""

    @property
    def cost(self) -> float:
        """取得原価の合計 (円)。"""
        return self.market_value - self.pnl

    @property
    def avg_cost(self) -> Optional[float]:
        """取得単価。株数が無い資産では None。"""
        if self.shares <= 0:
            return None
        return self.cost / self.shares

    @property
    def snapshot_price(self) -> Optional[float]:
        """スクリーンショット時点の 1 株あたり評価額。

        ライブ株価と大きく乖離していたら株式分割を疑う材料になる。
        """
        if self.shares <= 0:
            return None
        return self.market_value / self.shares

    @property
    def pnl_pct(self) -> Optional[float]:
        """取得原価に対する損益率 (%)。"""
        if self.cost <= 0:
            return None
        return self.pnl / self.cost * 100.0

    @property
    def priceable(self) -> bool:
        """市場価格を取りに行ける種別かどうか。"""
        return self.kind in (AssetKind.JP_STOCK, AssetKind.US_STOCK, AssetKind.FUND)

    @property
    def data_symbol(self) -> Optional[str]:
        """価格取得に使うシンボル。FUND は連動指数で代用する。"""
        if self.kind is AssetKind.FUND:
            return self.proxy_symbol
        return self.symbol


@dataclass(frozen=True)
class Quote:
    """1 銘柄の現在値スナップショット。"""

    symbol: str
    price: float
    previous_close: Optional[float] = None
    currency: str = "JPY"
    as_of: Optional[datetime] = None

    @property
    def change_pct(self) -> Optional[float]:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100.0


@dataclass
class Indicators:
    """テクニカル指標の計算結果。値が取れなかった項目は None。"""

    price: float
    sma25: Optional[float] = None
    sma75: Optional[float] = None
    sma200: Optional[float] = None
    rsi14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    atr14: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    volume_ratio: Optional[float] = None   # 直近出来高 / 20日平均出来高
    return_1m: Optional[float] = None      # %
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None
    return_12m: Optional[float] = None
    volatility: Optional[float] = None     # 年率換算 %

    @property
    def drawdown_from_52w_high(self) -> Optional[float]:
        """52週高値からの下落率 (%, 負の値)。"""
        if not self.high_52w:
            return None
        return (self.price - self.high_52w) / self.high_52w * 100.0


@dataclass
class Reason:
    """判定根拠の 1 項目。score は正なら売り方向、負なら買い方向。"""

    label: str
    score: float
    detail: str = ""


@dataclass
class Verdict:
    """1 銘柄の売り時診断結果。"""

    holding: Holding
    quote: Optional[Quote]
    indicators: Optional[Indicators]
    action: Action
    sell_score: float = 0.0
    reasons: list[Reason] = field(default_factory=list)
    stop_price: Optional[float] = None      # 推奨損切りライン
    target_price: Optional[float] = None    # 目安の利確ライン
    warnings: list[str] = field(default_factory=list)

    @property
    def live_pnl_pct(self) -> Optional[float]:
        """ライブ株価ベースの損益率 (%)。"""
        avg = self.holding.avg_cost
        if avg is None or not avg or self.quote is None:
            return self.holding.pnl_pct
        return (self.quote.price - avg) / avg * 100.0

    @property
    def live_market_value(self) -> Optional[float]:
        if self.quote is None or self.holding.shares <= 0:
            return self.holding.market_value
        return self.quote.price * self.holding.shares


@dataclass
class ScreenHit:
    """スクリーニングでヒットした銘柄。"""

    symbol: str
    name: str
    sector: str
    price: float
    score: float
    indicators: Indicators
    reasons: list[Reason] = field(default_factory=list)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    """発注リクエスト。金額指定 (amount) と株数指定 (qty) の両対応。"""

    symbol: str
    side: OrderSide
    qty: Optional[float] = None
    amount: Optional[float] = None
    limit_price: Optional[float] = None   # None なら成行
    reason: str = ""

    def __post_init__(self) -> None:
        if self.qty is None and self.amount is None:
            raise ValueError("qty か amount のどちらかは必須です")


@dataclass
class Fill:
    """約定結果。"""

    order: Order
    filled_qty: float
    filled_price: float
    fee: float = 0.0
    at: Optional[datetime] = None
    broker_order_id: Optional[str] = None
    note: str = ""


@dataclass
class Position:
    """ブローカー側が持っているポジション。"""

    symbol: str
    qty: float
    avg_price: float

    @property
    def cost(self) -> float:
        return self.qty * self.avg_price


@dataclass
class Bar:
    """日足 1 本。バックテストのテストデータ生成などで使う。"""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
