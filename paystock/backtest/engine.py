"""日足バックテスト。

自動売買を実弾で回す前に「そのルールが過去どう振る舞ったか」を確認するためのもの。

割り切っていること:
  - 判定は終値で行い、約定は翌営業日の始値とする (終値で判定して終値で買うのは
    実運用では不可能なので、その分だけ現実に寄せている)。
  - 手数料とスリッページは率で一律に引く。
  - 1 銘柄ずつ独立に検証する。銘柄間の資金の取り合いは考慮しない。

バックテストの結果が良くても将来の成績は保証されない。特にこのルールは
上昇相場で有利に出やすいので、下落局面を含む期間で必ず確認すること。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis import indicators as ind_mod
from ..analysis.rules import RuleConfig, evaluate
from ..models import Action, Indicators


@dataclass
class Trade:
    entry_day: pd.Timestamp
    entry_price: float
    exit_day: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""

    @property
    def return_pct(self) -> float | None:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0


@dataclass
class BacktestResult:
    symbol: str
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: list[Trade] = field(default_factory=list)
    buy_hold_return: float = 0.0
    initial_cash: float = 1_000_000.0

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if len(self.equity_curve) else self.initial_cash

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.initial_cash) / self.initial_cash * 100.0

    @property
    def cagr(self) -> float | None:
        if len(self.equity_curve) < 2:
            return None
        years = (self.equity_curve.index[-1] - self.equity_curve.index[0]).days / 365.25
        if years <= 0 or self.final_equity <= 0:
            return None
        return ((self.final_equity / self.initial_cash) ** (1 / years) - 1) * 100.0

    @property
    def max_drawdown(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        peak = self.equity_curve.cummax()
        return float(((self.equity_curve - peak) / peak).min() * 100.0)

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.exit_price is not None]

    @property
    def win_rate(self) -> float | None:
        closed = self.closed_trades
        if not closed:
            return None
        wins = sum(1 for t in closed if (t.return_pct or 0) > 0)
        return wins / len(closed) * 100.0

    @property
    def profit_factor(self) -> float | None:
        closed = self.closed_trades
        gains = sum(t.return_pct for t in closed if (t.return_pct or 0) > 0)
        losses = -sum(t.return_pct for t in closed if (t.return_pct or 0) <= 0)
        if losses <= 0:
            return None if gains <= 0 else float("inf")
        return gains / losses

    def summary(self) -> str:
        def fmt(value: float | None, suffix: str = "%") -> str:
            if value is None:
                return "－"
            if value == float("inf"):
                return "∞"
            return f"{value:,.2f}{suffix}"

        return "\n".join(
            [
                f"■ {self.symbol}",
                f"   期間            : {self.equity_curve.index[0]:%Y-%m-%d} 〜 "
                f"{self.equity_curve.index[-1]:%Y-%m-%d}"
                if len(self.equity_curve)
                else "   期間            : －",
                f"   最終資産        : {self.final_equity:,.0f} 円 "
                f"(初期 {self.initial_cash:,.0f} 円)",
                f"   トータルリターン: {fmt(self.total_return)}",
                f"   年率リターン    : {fmt(self.cagr)}",
                f"   最大ドローダウン: {fmt(self.max_drawdown)}",
                f"   売買回数        : {len(self.closed_trades)} 回",
                f"   勝率            : {fmt(self.win_rate)}",
                f"   プロフィットファクタ: {fmt(self.profit_factor, '')}",
                f"   単純保有した場合: {fmt(self.buy_hold_return)}",
            ]
        )


def _indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """全指標を列としてまとめて計算する (1 行ずつ再計算しないため)。"""
    close = df["close"]
    macd_line, macd_sig, macd_hist = ind_mod.macd(close)
    bb_low, _mid, bb_high = ind_mod.bollinger(close)

    out = pd.DataFrame(index=df.index)
    out["price"] = close
    out["sma25"] = ind_mod.sma(close, 25)
    out["sma75"] = ind_mod.sma(close, 75)
    out["sma200"] = ind_mod.sma(close, 200)
    out["rsi14"] = ind_mod.rsi(close, 14)
    out["macd"] = macd_line
    out["macd_signal"] = macd_sig
    out["macd_hist"] = macd_hist
    out["macd_hist_prev"] = macd_hist.shift(1)
    out["bb_upper"] = bb_high
    out["bb_lower"] = bb_low
    out["atr14"] = ind_mod.atr(df, 14)
    window = min(len(df), ind_mod.TRADING_DAYS_PER_YEAR)
    out["high_52w"] = df["high"].rolling(window, min_periods=20).max()
    out["low_52w"] = df["low"].rolling(window, min_periods=20).min()
    avg_volume = df["volume"].rolling(20, min_periods=20).mean()
    out["volume_ratio"] = (df["volume"] / avg_volume).replace([np.inf, -np.inf], np.nan)
    return out


def _row_to_indicators(row: pd.Series) -> Indicators | None:
    """指標行を Indicators に変換する。主要指標が欠けている行は None。"""
    if pd.isna(row["price"]) or pd.isna(row["sma25"]) or pd.isna(row["atr14"]):
        return None

    def opt(key: str) -> float | None:
        value = row[key]
        return None if pd.isna(value) else float(value)

    return Indicators(
        price=float(row["price"]),
        sma25=opt("sma25"),
        sma75=opt("sma75"),
        sma200=opt("sma200"),
        rsi14=opt("rsi14"),
        macd=opt("macd"),
        macd_signal=opt("macd_signal"),
        macd_hist=opt("macd_hist"),
        macd_hist_prev=opt("macd_hist_prev"),
        bb_upper=opt("bb_upper"),
        bb_lower=opt("bb_lower"),
        atr14=opt("atr14"),
        high_52w=opt("high_52w"),
        low_52w=opt("low_52w"),
        volume_ratio=opt("volume_ratio"),
    )


def run(
    symbol: str,
    df: pd.DataFrame,
    rules: RuleConfig | None = None,
    initial_cash: float = 1_000_000.0,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.1,
) -> BacktestResult:
    """1 銘柄をバックテストする。

    ルールが BUY_MORE を出したら全力で買い、SELL / STOP_LOSS で全部売る。
    TRIM は保有の 1/3 を売る。
    """
    rules = rules or RuleConfig()
    df = df.sort_index()
    if len(df) < 60:
        raise ValueError(f"{symbol}: バックテストに必要な日数が足りません ({len(df)} 日)")

    ind_frame = _indicator_frame(df)

    cash = initial_cash
    shares = 0.0
    avg_cost = 0.0
    trades: list[Trade] = []
    equity_index: list[pd.Timestamp] = []
    equity_values: list[float] = []
    pending: tuple[str, float] | None = None  # (side, ratio) を翌日の始値で執行する

    for i, day in enumerate(df.index):
        open_price = float(df["open"].iloc[i])

        # 前日終値で出した判断を、当日の始値で執行する
        if pending is not None:
            side, ratio = pending
            pending = None
            if side == "buy" and cash > 0:
                price = open_price * (1 + slippage_pct / 100.0)
                # 手数料も現金から出るので、その分を差し引いた額を約定代金に充てる。
                # 単純に cash * ratio を代金にすると、手数料が乗った瞬間に
                # 残高を超えて全期間エントリーできなくなる。
                budget = cash * ratio / (1.0 + fee_pct / 100.0)
                qty = budget / price
                fee = budget * fee_pct / 100.0
                if qty > 0 and budget + fee <= cash + 1e-9:
                    avg_cost = (
                        (shares * avg_cost + qty * price) / (shares + qty)
                        if shares + qty > 0
                        else price
                    )
                    shares += qty
                    cash -= budget + fee
                    trades.append(Trade(entry_day=day, entry_price=price))
            elif side == "sell" and shares > 0:
                price = open_price * (1 - slippage_pct / 100.0)
                qty = shares * ratio
                proceeds = qty * price
                cash += proceeds - proceeds * fee_pct / 100.0
                shares -= qty
                for t in reversed(trades):
                    if t.exit_price is None:
                        t.exit_day = day
                        t.exit_price = price
                        break
                if shares <= 1e-9:
                    shares = 0.0
                    avg_cost = 0.0

        ind = _row_to_indicators(ind_frame.iloc[i])
        if ind is not None:
            pnl_pct = (
                (ind.price - avg_cost) / avg_cost * 100.0 if shares > 0 and avg_cost else None
            )
            action, _score, _reasons, _stop, _target = evaluate(ind, rules, pnl_pct)

            if shares > 0 and action in (Action.SELL, Action.STOP_LOSS):
                pending = ("sell", 1.0)
            elif shares > 0 and action is Action.TRIM:
                pending = ("sell", 1 / 3)
            elif shares == 0 and action is Action.BUY_MORE:
                pending = ("buy", 1.0)

        equity_index.append(day)
        equity_values.append(cash + shares * float(df["close"].iloc[i]))

    first_close = float(df["close"].iloc[0])
    last_close = float(df["close"].iloc[-1])

    return BacktestResult(
        symbol=symbol,
        equity_curve=pd.Series(equity_values, index=pd.DatetimeIndex(equity_index)),
        trades=trades,
        buy_hold_return=(last_close - first_close) / first_close * 100.0,
        initial_cash=initial_cash,
    )
