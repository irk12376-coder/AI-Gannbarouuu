"""通知の送信 (Webhook / メール)。

手動で売買するなら、レポートを見に行かなくても届く仕組みが要る。
対応の必要がない日は送らないので、通知が来た = 何かある、という状態を保つ。

宛先は環境変数で渡す。設定ファイルに URL やパスワードを書くと、
うっかりコミットして漏らす事故が起きるため。

  PAYSTOCK_WEBHOOK_URL  Slack か Discord の Incoming Webhook URL
  PAYSTOCK_SMTP_HOST    SMTP サーバ (例: smtp.gmail.com)
  PAYSTOCK_SMTP_PORT    ポート (既定 587)
  PAYSTOCK_SMTP_USER    ユーザ名
  PAYSTOCK_SMTP_PASS    パスワード (Gmail ならアプリパスワード)
  PAYSTOCK_MAIL_TO      宛先メールアドレス
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

# Discord の Webhook は末尾が異なるだけで、送信形式も {"content": ...} で受け付ける
_DISCORD_HOSTS = ("discord.com", "discordapp.com")


class NotifyError(RuntimeError):
    """通知の送信に失敗したときに投げる。"""


def _payload_for(url: str, subject: str, body: str) -> dict:
    """Webhook の種類に応じた本文を作る。"""
    text = f"{subject}\n\n{body}"
    if any(host in url for host in _DISCORD_HOSTS):
        # Discord は 2000 文字を超えると 400 を返す
        return {"content": text[:1900]}
    return {"text": text}


def send_webhook(subject: str, body: str, url: str | None = None) -> bool:
    """Slack / Discord の Incoming Webhook に送る。送ったら True。"""
    url = url or os.environ.get("PAYSTOCK_WEBHOOK_URL", "")
    if not url:
        return False

    import requests  # noqa: PLC0415 — 通知を使うときだけ必要

    try:
        res = requests.post(url, json=_payload_for(url, subject, body), timeout=15)
        res.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise NotifyError(f"Webhook の送信に失敗しました: {exc}") from exc
    return True


def send_mail(subject: str, body: str) -> bool:
    """SMTP でメールを送る。設定が揃っていなければ何もせず False。"""
    host = os.environ.get("PAYSTOCK_SMTP_HOST", "")
    user = os.environ.get("PAYSTOCK_SMTP_USER", "")
    password = os.environ.get("PAYSTOCK_SMTP_PASS", "")
    to = os.environ.get("PAYSTOCK_MAIL_TO", "")
    if not (host and user and password and to):
        return False

    port = int(os.environ.get("PAYSTOCK_SMTP_PORT", "587"))
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001
        raise NotifyError(f"メールの送信に失敗しました: {exc}") from exc
    return True


def notify(subject: str, body: str) -> list[str]:
    """設定されている経路すべてに送り、送れた経路名を返す。

    片方が失敗してももう片方は試す。通知が 1 つも届かないより、
    1 つでも届くほうがましなため。
    """
    sent: list[str] = []
    for name, sender in (("webhook", send_webhook), ("mail", send_mail)):
        try:
            if sender(subject, body):
                sent.append(name)
        except NotifyError as exc:
            log.warning("%s", exc)
    return sent
