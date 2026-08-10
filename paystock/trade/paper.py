"""ペーパートレード用ブローカー (実際の資金は動かない)。

自動売買を実弾で回す前に、必ずこれで十分な期間の検証を行うためのもの。
状態は JSON で永続化するので、cron で毎日回して結果を追跡できる。

約定価格は「現在値 + スリッページ」で単純化している。実際の板を無視するため、
流動性の低い銘柄では楽観的な結果になる点に注意。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..data import PriceSource, PriceSourceError
from ..models import Fill, Order, OrderSide, Position
from .base import Broker, BrokerError

DEFAULT_STATE_PATH = Path(".paystock/paper_account.json")


class PaperBroker(Broker):
    name = "paper"
    supports_amount_orders = True  # PayPay 証券と同じく金額指定を許す

    def __init__(
        self,
        source: PriceSource,
        state_path: Path | str = DEFAULT_STATE_PATH,
        initial_cash: float = 100_000.0,
        slippage_pct: float = 0.1,
        fee_pct: float = 0.0,
    ) -> None:
        self.source = source
        self.state_path = Path(state_path)
        self.slippage_pct = slippage_pct
        self.fee_pct = fee_pct
        self._price_cache: dict[str, float] = {}
        self._state = self._load(initial_cash)

    # ------------------------------------------------------------------ 永続化

    def _load(self, initial_cash: float) -> dict:
        if self.state_path.exists():
            with self.state_path.open(encoding="utf-8") as fp:
                return json.load(fp)
        return {"cash": initial_cash, "positions": {}, "history": []}

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as fp:
            json.dump(self._state, fp, ensure_ascii=False, indent=2)

    def reset(self, initial_cash: float = 100_000.0) -> None:
        self._state = {"cash": initial_cash, "positions": {}, "history": []}
        self.save()

    # -------------------------------------------------------------- 照会 API

    def cash(self) -> float:
        return float(self._state["cash"])

    def positions(self) -> list[Position]:
        return [
            Position(symbol=sym, qty=float(p["qty"]), avg_price=float(p["avg_price"]))
            for sym, p in self._state["positions"].items()
            if float(p["qty"]) > 0
        ]

    def last_price(self, symbol: str) -> float:
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        try:
            price = self.source.quote(symbol).price
        except PriceSourceError as exc:
            raise BrokerError(f"{symbol}: 現在値を取得できません ({exc})") from exc
        self._price_cache[symbol] = price
        return price

    @property
    def history(self) -> list[dict]:
        return list(self._state["history"])

    # ---------------------------------------------------------------- 発注

    def _execution_price(self, symbol: str, side: OrderSide) -> float:
        """スリッページ込みの約定価格。買いは不利側、売りも不利側に寄せる。"""
        price = self.last_price(symbol)
        drift = price * self.slippage_pct / 100.0
        return price + drift if side is OrderSide.BUY else price - drift

    def submit(self, order: Order) -> Fill:
        price = self._execution_price(order.symbol, order.side)
        if price <= 0:
            raise BrokerError(f"{order.symbol}: 価格が不正です ({price})")

        if order.limit_price is not None:
            unfillable = (
                order.side is OrderSide.BUY and price > order.limit_price
            ) or (order.side is OrderSide.SELL and price < order.limit_price)
            if unfillable:
                raise BrokerError(
                    f"{order.symbol}: 指値 {order.limit_price:,.1f} に対して"
                    f"約定価格 {price:,.1f} のため約定しません"
                )

        qty = order.qty if order.qty is not None else (order.amount or 0.0) / price
        if qty <= 0:
            raise BrokerError(f"{order.symbol}: 数量が 0 です")

        book = self._state["positions"].setdefault(
            order.symbol, {"qty": 0.0, "avg_price": 0.0}
        )
        gross = qty * price
        fee = gross * self.fee_pct / 100.0

        if order.side is OrderSide.BUY:
            if gross + fee > self.cash():
                raise BrokerError(
                    f"{order.symbol}: 現金不足 (必要 {gross + fee:,.0f} 円 / "
                    f"残高 {self.cash():,.0f} 円)"
                )
            held = float(book["qty"])
            book["avg_price"] = (
                (held * float(book["avg_price"]) + gross) / (held + qty)
                if held + qty > 0
                else price
            )
            book["qty"] = held + qty
            self._state["cash"] = self.cash() - gross - fee
        else:
            held = float(book["qty"])
            if qty > held + 1e-9:
                raise BrokerError(
                    f"{order.symbol}: 保有 {held:.4f} 株 に対して {qty:.4f} 株 の売却はできません"
                )
            book["qty"] = max(held - qty, 0.0)
            self._state["cash"] = self.cash() + gross - fee

        fill = Fill(
            order=order,
            filled_qty=qty,
            filled_price=price,
            fee=fee,
            at=datetime.now(),
            broker_order_id=f"paper-{len(self._state['history']) + 1}",
        )
        self._state["history"].append(
            {
                "at": fill.at.isoformat(timespec="seconds"),
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": qty,
                "price": price,
                "fee": fee,
                "reason": order.reason,
            }
        )
        self.save()
        return fill
