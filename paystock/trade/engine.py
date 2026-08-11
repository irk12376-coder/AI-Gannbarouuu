"""自動売買エンジンと安全装置。

設計の前提: **止まる側に倒す**。
判断に迷う状況・情報が欠けている状況では発注しない。自動売買で一番怖いのは
「儲け損ねること」ではなく「想定外の状況で機械が売買を続けること」なので、
RiskGuard が 1 つでも NG を出したらその注文は捨てる。

安全装置の一覧:
  - キルスイッチ: 指定ファイルが存在したら一切発注しない
  - ドライラン: 既定 ON。明示的に切らない限り発注は行われない
  - 立会時間チェック: 東証の取引時間外は動かさない
  - 1 発注あたりの上限金額 / 1 銘柄あたりの上限比率
  - 1 日あたりの発注回数上限
  - 当日の損失上限 (超えたらその日は全面停止)
  - 現金バッファ: これを下回る現金は買いに使わない
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from ..analysis import indicators as ind_mod
from ..analysis.rules import RuleConfig, evaluate
from ..config import TradeSettings
from ..data import PriceSource, PriceSourceError
from ..models import Action, Fill, Order, OrderSide
from .base import Broker, BrokerError

log = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MORNING = (time(9, 0), time(11, 30))
AFTERNOON = (time(12, 30), time(15, 30))

DEFAULT_JOURNAL = Path(".paystock/journal.jsonl")
DEFAULT_STATE = Path(".paystock/engine_state.json")


def is_market_open(now: datetime | None = None) -> bool:
    """東証の立会時間内かどうか。

    祝日は判定していないので、祝日に動かしたくない場合は cron 側で除外するか、
    祝日カレンダーを別途組み込むこと。
    """
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    now = now.astimezone(JST)

    if now.weekday() >= 5:  # 土日
        return False
    t = now.time()
    return (MORNING[0] <= t <= MORNING[1]) or (AFTERNOON[0] <= t <= AFTERNOON[1])


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


@dataclass
class DailyState:
    """当日の発注回数と基準資産額。日付が変わったらリセットする。"""

    day: str = ""
    order_count: int = 0
    start_equity: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "DailyState":
        if path.exists():
            try:
                with path.open(encoding="utf-8") as fp:
                    return cls(**json.load(fp))
            except Exception:  # noqa: BLE001 — 壊れていたら初期状態に戻す
                log.warning("%s を読めなかったので初期化します", path)
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(self.__dict__, fp, ensure_ascii=False, indent=2)

    def roll(self, today: str, equity: float) -> None:
        """日付が変わっていたらカウンタを初期化する。"""
        if self.day != today:
            self.day = today
            self.order_count = 0
            self.start_equity = equity


class RiskGuard:
    """発注前のチェック一式。"""

    def __init__(self, settings: TradeSettings, state_path: Path | str = DEFAULT_STATE) -> None:
        self.s = settings
        self.state_path = Path(state_path)
        self.state = DailyState.load(self.state_path)

    def begin_session(self, equity: float, now: datetime | None = None) -> GuardResult:
        """セッション全体を止めるべきかを判定する。"""
        now = now or datetime.now(JST)
        self.state.roll(now.astimezone(JST).strftime("%Y-%m-%d"), equity)
        self.state.save(self.state_path)

        if Path(self.s.kill_switch_file).exists():
            return GuardResult(
                False,
                f"キルスイッチ {self.s.kill_switch_file} が存在するため停止します",
            )
        if not self.s.enabled:
            return GuardResult(False, "trade.enabled が false のため発注しません")
        if self.s.trading_hours_only and not is_market_open(now):
            return GuardResult(False, "東証の立会時間外のため発注しません")

        if self.state.start_equity > 0:
            change = (equity - self.state.start_equity) / self.state.start_equity * 100.0
            if change <= -abs(self.s.max_daily_loss_pct):
                return GuardResult(
                    False,
                    f"当日の損失が {change:.2f}% となり上限 "
                    f"{-abs(self.s.max_daily_loss_pct):.2f}% を超えたため全面停止します",
                )
        return GuardResult(True)

    def check_order(self, order: Order, amount: float, equity: float, broker: Broker) -> GuardResult:
        """個別注文のチェック。"""
        if self.state.order_count >= self.s.max_orders_per_day:
            return GuardResult(
                False,
                f"当日の発注回数が上限 {self.s.max_orders_per_day} 回に達しています",
            )
        if amount > self.s.max_order_amount:
            return GuardResult(
                False,
                f"発注金額 {amount:,.0f} 円 が 1 回の上限 "
                f"{self.s.max_order_amount:,.0f} 円 を超えています",
            )

        if order.side is OrderSide.BUY:
            if broker.cash() - amount < self.s.min_cash_buffer:
                return GuardResult(
                    False,
                    f"買付後の現金が最低バッファ {self.s.min_cash_buffer:,.0f} 円 を下回ります",
                )
            existing = broker.position_for(order.symbol)
            current_value = 0.0
            if existing:
                try:
                    current_value = existing.qty * broker.last_price(order.symbol)
                except BrokerError:
                    current_value = existing.cost
            if equity > 0:
                after_pct = (current_value + amount) / equity * 100.0
                if after_pct > self.s.max_position_pct:
                    return GuardResult(
                        False,
                        f"{order.symbol} の比率が {after_pct:.1f}% となり上限 "
                        f"{self.s.max_position_pct:.1f}% を超えます",
                    )
        return GuardResult(True)

    def record_order(self) -> None:
        self.state.order_count += 1
        self.state.save(self.state_path)


@dataclass
class Decision:
    """1 銘柄に対する判断の記録。発注したかどうかに関わらず残す。"""

    symbol: str
    action: Action
    score: float
    executed: bool
    detail: str
    order: Order | None = None
    fill: Fill | None = None

    def as_dict(self) -> dict:
        return {
            "at": datetime.now(JST).isoformat(timespec="seconds"),
            "symbol": self.symbol,
            "action": self.action.value,
            "score": round(self.score, 1),
            "executed": self.executed,
            "detail": self.detail,
            "qty": self.fill.filled_qty if self.fill else None,
            "price": self.fill.filled_price if self.fill else None,
        }


@dataclass
class EngineResult:
    decisions: list[Decision] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""

    @property
    def executed(self) -> list[Decision]:
        return [d for d in self.decisions if d.executed]


class TradingEngine:
    """ルールに従って売買判断を出し、安全装置を通ったものだけ発注する。"""

    def __init__(
        self,
        broker: Broker,
        source: PriceSource,
        settings: TradeSettings,
        rules: RuleConfig | None = None,
        journal_path: Path | str = DEFAULT_JOURNAL,
        state_path: Path | str = DEFAULT_STATE,
    ) -> None:
        self.broker = broker
        self.source = source
        self.settings = settings
        self.rules = rules or RuleConfig()
        self.guard = RiskGuard(settings, state_path)
        self.journal_path = Path(journal_path)

    def _journal(self, decision: Decision) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(decision.as_dict(), ensure_ascii=False) + "\n")

    def _buy_amount(self, equity: float) -> float:
        """1 回の買付金額。上限金額と 1 銘柄あたりの比率上限の小さい方。"""
        by_pct = equity * self.settings.max_position_pct / 100.0
        return min(self.settings.max_order_amount, by_pct)

    def _make_order(self, symbol: str, action: Action, equity: float) -> tuple[Order | None, str]:
        """アクションを注文に変換する。発注不要なら (None, 理由)。"""
        position = self.broker.position_for(symbol)

        if action in (Action.STOP_LOSS, Action.SELL):
            if not position:
                return None, "保有していないため売却なし"
            return (
                Order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    qty=position.qty,
                    reason=action.value,
                ),
                "全株売却",
            )

        if action is Action.TRIM:
            if not position:
                return None, "保有していないため利確なし"
            qty = position.qty / 3.0  # 1/3 だけ利確して、残りは伸ばす
            if qty <= 0:
                return None, "利確できる数量がありません"
            return (
                Order(symbol=symbol, side=OrderSide.SELL, qty=qty, reason=action.value),
                "1/3 を利確",
            )

        if action is Action.BUY_MORE:
            amount = self._buy_amount(equity)
            if amount <= 0:
                return None, "買付可能額が 0 です"
            if self.broker.supports_amount_orders:
                return (
                    Order(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        amount=amount,
                        reason=action.value,
                    ),
                    f"{amount:,.0f} 円 を買付",
                )
            # 金額指定に対応しないブローカーでは単元株に丸める
            price = self.broker.last_price(symbol)
            units = int(amount // (price * 100))
            if units < 1:
                return None, (
                    f"1 単元 (100株 = 約 {price * 100:,.0f} 円) が"
                    f"上限金額 {amount:,.0f} 円 を超えるため見送り"
                )
            return (
                Order(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    qty=units * 100,
                    reason=action.value,
                ),
                f"{units * 100} 株 を買付",
            )

        return None, "シグナルなし"

    def run(self, symbols: list[str], now: datetime | None = None) -> EngineResult:
        """ウォッチリストを 1 巡して、必要な売買を実行する。"""
        result = EngineResult()

        try:
            equity = self.broker.equity()
        except BrokerError as exc:
            result.halted = True
            result.halt_reason = f"資産評価に失敗しました: {exc}"
            return result

        session = self.guard.begin_session(equity, now)
        if not session.ok:
            result.halted = True
            result.halt_reason = session.reason
            log.warning("エンジン停止: %s", session.reason)
            return result

        for symbol in symbols:
            try:
                df = self.source.history(symbol, days=400)
                ind = ind_mod.compute(df)
            except (PriceSourceError, ValueError, KeyError) as exc:
                log.warning("%s をスキップ: %s", symbol, exc)
                continue

            position = self.broker.position_for(symbol)
            pnl_pct = None
            if position and position.avg_price:
                pnl_pct = (ind.price - position.avg_price) / position.avg_price * 100.0

            action, score, _reasons, _stop, _target = evaluate(ind, self.rules, pnl_pct)
            order, detail = self._make_order(symbol, action, equity)

            if order is None:
                decision = Decision(symbol, action, score, False, detail)
                result.decisions.append(decision)
                continue

            amount = (
                order.amount
                if order.amount is not None
                else (order.qty or 0.0) * ind.price
            )
            check = self.guard.check_order(order, amount, equity, self.broker)
            if not check.ok:
                decision = Decision(symbol, action, score, False, f"見送り: {check.reason}", order)
                result.decisions.append(decision)
                self._journal(decision)
                continue

            if self.settings.dry_run:
                decision = Decision(
                    symbol, action, score, False, f"[ドライラン] {detail}", order
                )
                result.decisions.append(decision)
                self._journal(decision)
                continue

            try:
                fill = self.broker.submit(order)
            except BrokerError as exc:
                decision = Decision(symbol, action, score, False, f"発注失敗: {exc}", order)
                result.decisions.append(decision)
                self._journal(decision)
                continue

            self.guard.record_order()
            decision = Decision(symbol, action, score, True, detail, order, fill)
            result.decisions.append(decision)
            self._journal(decision)
            log.info(
                "%s %s %.4f株 @ %.1f (%s)",
                symbol,
                order.side.value,
                fill.filled_qty,
                fill.filled_price,
                detail,
            )

        return result
