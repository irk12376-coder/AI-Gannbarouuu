"""ブローカー共通インタフェース。

**PayPay 証券について**

PayPay 証券には一般利用者向けの公開注文 API が存在しない。
アプリの自動操作 (スクレイピング / UI 自動化) は利用規約に抵触する可能性が高く、
画面変更で簡単に壊れる上、誤発注時の責任も取れない。よってこのツールでは
PayPay 証券への自動発注は実装しない。PayPay 証券の口座については
「分析とアラート」までを担当し、発注は手動で行う前提とする。

自動売買を実際に動かす場合は、注文 API を公式に提供している証券会社
(本ツールでは auカブコム証券の kabu ステーション API) を使う。
まずは PaperBroker で十分な期間シミュレーションしてから移行すること。
"""

from __future__ import annotations

import abc

from ..models import Fill, Order, Position


class BrokerError(RuntimeError):
    """発注・照会の失敗。"""


class Broker(abc.ABC):
    """発注と資産照会の最小インタフェース。"""

    name: str = "base"
    supports_amount_orders: bool = False  # 金額指定注文に対応しているか

    @abc.abstractmethod
    def cash(self) -> float:
        """発注可能な現金 (円)。"""

    @abc.abstractmethod
    def positions(self) -> list[Position]:
        """保有ポジション一覧。"""

    @abc.abstractmethod
    def last_price(self, symbol: str) -> float:
        """現在値。発注数量の計算と評価に使う。"""

    @abc.abstractmethod
    def submit(self, order: Order) -> Fill:
        """注文を出して約定結果を返す。"""

    def position_for(self, symbol: str) -> Position | None:
        return next((p for p in self.positions() if p.symbol == symbol), None)

    def equity(self) -> float:
        """現金 + ポジション評価額。"""
        total = self.cash()
        for p in self.positions():
            try:
                total += p.qty * self.last_price(p.symbol)
            except BrokerError:
                total += p.cost  # 値が引けないぶんは取得原価で代用する
        return total
