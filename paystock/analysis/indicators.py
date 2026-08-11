"""テクニカル指標の計算。

入力はすべて `open/high/low/close/volume` 列を持つ日足 DataFrame (index は日付昇順)。
外部ネットワークに触らないので、合成データで単体テストできる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..models import Indicators

TRADING_DAYS_PER_YEAR = 245  # 東証のおおよその年間営業日数


def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder の RSI。

    最初の window 本は単純平均、以降は Wilder の平滑化 (alpha = 1/window)。
    pandas の ewm(alpha=1/window) がまさにその平滑化なので、それを使う。
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    # 下落が皆無だと 0 除算になるので、その場合は RSI=100 に倒す
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.where(avg_loss != 0.0, 100.0).where(avg_gain.notna())


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD / シグナル / ヒストグラムを返す。"""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(
    close: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ボリンジャーバンド (下限, 中心, 上限)。"""
    mid = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std(ddof=0)
    return mid - num_std * sd, mid, mid + num_std * sd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder の ATR。損切り幅の基準に使う。"""
    return true_range(df).ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()


def realized_volatility(close: pd.Series, window: int = 60) -> pd.Series:
    """年率換算のヒストリカル・ボラティリティ (%)。"""
    ret = np.log(close / close.shift(1))
    return ret.rolling(window, min_periods=window).std(ddof=0) * np.sqrt(
        TRADING_DAYS_PER_YEAR
    ) * 100.0


def _pct_return(close: pd.Series, lookback: int) -> float | None:
    """lookback 営業日前からの騰落率 (%)。データ不足なら None。"""
    if len(close) <= lookback:
        return None
    past = close.iloc[-1 - lookback]
    if not past or not np.isfinite(past):
        return None
    return float((close.iloc[-1] - past) / past * 100.0)


def _last(series: pd.Series, offset: int = 0) -> float | None:
    """末尾から offset 本前の値。NaN / 範囲外は None。"""
    idx = len(series) - 1 - offset
    if idx < 0:
        return None
    value = series.iloc[idx]
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def compute(df: pd.DataFrame) -> Indicators:
    """日足 DataFrame から全指標を計算する。

    データが短い場合は計算できた指標だけが埋まり、残りは None になる。
    """
    if df is None or df.empty:
        raise ValueError("空の価格データからは指標を計算できません")

    df = df.sort_index()
    close = df["close"].astype(float)

    macd_line, macd_sig, macd_hist = macd(close)
    bb_low, _bb_mid, bb_high = bollinger(close)
    window_52w = min(len(close), TRADING_DAYS_PER_YEAR)
    recent_52w = df.iloc[-window_52w:]

    volume_ratio = None
    if "volume" in df and len(df) >= 20:
        avg_volume = float(df["volume"].iloc[-20:].mean())
        last_volume = float(df["volume"].iloc[-1])
        if avg_volume > 0:
            volume_ratio = last_volume / avg_volume

    return Indicators(
        price=float(close.iloc[-1]),
        sma25=_last(sma(close, 25)),
        sma75=_last(sma(close, 75)),
        sma200=_last(sma(close, 200)),
        rsi14=_last(rsi(close, 14)),
        macd=_last(macd_line),
        macd_signal=_last(macd_sig),
        macd_hist=_last(macd_hist),
        macd_hist_prev=_last(macd_hist, offset=1),
        bb_upper=_last(bb_high),
        bb_lower=_last(bb_low),
        atr14=_last(atr(df, 14)),
        high_52w=float(recent_52w["high"].max()),
        low_52w=float(recent_52w["low"].min()),
        volume_ratio=volume_ratio,
        return_1m=_pct_return(close, 21),
        return_3m=_pct_return(close, 63),
        return_6m=_pct_return(close, 126),
        return_12m=_pct_return(close, 245),
        volatility=_last(realized_volatility(close)),
    )
