"""auカブコム証券 kabu ステーション API アダプタ。

国内で個人が「公式に」自動売買できる数少ない経路のひとつ。
ローカル PC で kabu ステーションを起動しておくと、そのアプリが
localhost に REST サーバを立てるので、そこへ HTTP で注文を出す。

  本番環境 : http://localhost:18080/kabusapi
  検証環境 : http://localhost:18081/kabusapi

前提条件 (すべて利用者側で用意する必要がある):
  1. auカブコム証券の口座を開設する
  2. kabu ステーションをインストールし、API 利用を申し込む
  3. kabu ステーション側で API パスワードと注文パスワードを設定する
  4. kabu ステーションを起動したまま、同じ PC でこのツールを動かす

PayPay 証券との重要な違い:
  - 金額指定の小数株注文はできない。原則 100 株単位 (単元株) になる。
    そのため 1 銘柄あたりの必要資金が大きく、少額での分散は難しい。
  - 現物取引を前提に実装している (信用取引は扱わない)。

API の仕様は auカブコム証券の公式ドキュメントが正であり、変更されうる。
実弾を入れる前に必ず検証環境 (ポート 18081) で動作を確認すること。
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import Fill, Order, OrderSide, Position
from .base import Broker, BrokerError

log = logging.getLogger(__name__)

PROD_BASE = "http://localhost:18080/kabusapi"
TEST_BASE = "http://localhost:18081/kabusapi"

# 東証 = 1。他の市場に出す場合は公式ドキュメントの Exchange 定義を参照。
EXCHANGE_TSE = 1

_SIDE_CODE = {OrderSide.SELL: "1", OrderSide.BUY: "2"}
_MARKET_ORDER = 10  # FrontOrderType: 成行
_LIMIT_ORDER = 20   # FrontOrderType: 指値


class KabusBroker(Broker):
    name = "kabus"
    supports_amount_orders = False  # 単元株単位でしか出せない

    def __init__(
        self,
        api_password: str,
        order_password: str,
        base_url: str = TEST_BASE,
        exchange: int = EXCHANGE_TSE,
        account_type: int = 4,   # 4 = 特定口座
        deliv_type: int = 2,     # 2 = お預り金から充当
        timeout: int = 10,
    ) -> None:
        if not api_password or not order_password:
            raise BrokerError("API パスワードと注文パスワードの両方が必要です")
        self.api_password = api_password
        self.order_password = order_password
        self.base_url = base_url.rstrip("/")
        self.exchange = exchange
        self.account_type = account_type
        self.deliv_type = deliv_type
        self.timeout = timeout
        self._token: str | None = None

    # ------------------------------------------------------------ HTTP 基盤

    def _request(self, method: str, path: str, **kwargs):
        import requests  # noqa: PLC0415 — kabus を使うときだけ必要

        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        if self._token and path != "/token":
            headers["X-API-KEY"] = self._token

        try:
            res = requests.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(
                f"kabu ステーションに接続できません ({url})。"
                "アプリが起動しているか、ポート番号が正しいか確認してください。"
                f" 詳細: {exc}"
            ) from exc

        if res.status_code >= 400:
            raise BrokerError(f"{path}: HTTP {res.status_code} — {res.text[:300]}")

        payload = res.json()
        # 一覧系はリストで返るので、その場合はエラーコード判定をしない
        if isinstance(payload, dict) and payload.get("Code") not in (None, 0):
            raise BrokerError(
                f"{path}: API エラー Code={payload.get('Code')} "
                f"{payload.get('Message', '')}"
            )
        return payload

    def connect(self) -> None:
        """API トークンを取得する。他の呼び出しの前に必要。"""
        payload = self._request(
            "POST", "/token", json={"APIPassword": self.api_password}
        )
        token = payload.get("Token")
        if not token:
            raise BrokerError(f"トークンを取得できませんでした: {payload}")
        self._token = token
        log.info("kabu ステーション API に接続しました (%s)", self.base_url)

    def _ensure_token(self) -> None:
        if not self._token:
            self.connect()

    # -------------------------------------------------------------- 照会

    def cash(self) -> float:
        self._ensure_token()
        payload = self._request("GET", "/wallet/cash")
        return float(payload.get("StockAccountWallet", 0.0))

    def positions(self) -> list[Position]:
        self._ensure_token()
        payload = self._request("GET", "/positions", params={"product": 1})
        out: list[Position] = []
        for row in payload or []:
            qty = float(row.get("LeavesQty") or row.get("Qty") or 0.0)
            if qty <= 0:
                continue
            out.append(
                Position(
                    symbol=str(row.get("Symbol")),
                    qty=qty,
                    avg_price=float(row.get("Price") or 0.0),
                )
            )
        return out

    def last_price(self, symbol: str) -> float:
        self._ensure_token()
        payload = self._request("GET", f"/board/{symbol}@{self.exchange}")
        price = payload.get("CurrentPrice")
        if price is None:
            raise BrokerError(f"{symbol}: 現在値が板情報に含まれていません")
        return float(price)

    # -------------------------------------------------------------- 発注

    def submit(self, order: Order) -> Fill:
        self._ensure_token()

        if order.qty is None:
            raise BrokerError(
                "kabu ステーション API は金額指定注文に対応していません。"
                "qty (株数) を指定してください。"
            )
        qty = int(order.qty)
        if qty <= 0:
            raise BrokerError(f"{order.symbol}: 発注株数が 0 です")
        if qty % 100 != 0:
            log.warning(
                "%s: %d 株 は単元株 (100株) の倍数ではありません。"
                "単元未満株の取扱可否は口座設定に依存します。",
                order.symbol,
                qty,
            )

        body = {
            "Password": self.order_password,
            "Symbol": order.symbol,
            "Exchange": self.exchange,
            "SecurityType": 1,          # 株式
            "Side": _SIDE_CODE[order.side],
            "CashMargin": 1,            # 現物
            "DelivType": self.deliv_type if order.side is OrderSide.BUY else 0,
            "AccountType": self.account_type,
            "Qty": qty,
            "FrontOrderType": _MARKET_ORDER if order.limit_price is None else _LIMIT_ORDER,
            "Price": 0 if order.limit_price is None else float(order.limit_price),
            "ExpireDay": 0,             # 0 = 当日中
        }
        payload = self._request("POST", "/sendorder", json=body)

        # sendorder は受付 ID を返すだけで、約定価格はこの時点では分からない。
        # 参考値として板の現在値を入れておく。
        try:
            reference_price = self.last_price(order.symbol)
        except BrokerError:
            reference_price = order.limit_price or 0.0

        return Fill(
            order=order,
            filled_qty=float(qty),
            filled_price=reference_price,
            at=datetime.now(),
            broker_order_id=str(payload.get("OrderId", "")),
            note="発注を受け付けました。約定状況は kabu ステーション側で確認してください。",
        )
