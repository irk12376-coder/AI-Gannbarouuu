"""通知まわりのテスト。

実際の送信はしない。「宛先が無ければ静かに何もしない」ことと、
「片方が落ちてももう片方は試す」ことを確認する。
"""

from __future__ import annotations

import pytest

from paystock import notify as notify_mod


def clear_env(monkeypatch):
    for key in (
        "PAYSTOCK_WEBHOOK_URL",
        "PAYSTOCK_SMTP_HOST",
        "PAYSTOCK_SMTP_USER",
        "PAYSTOCK_SMTP_PASS",
        "PAYSTOCK_MAIL_TO",
    ):
        monkeypatch.delenv(key, raising=False)


def test_webhook_is_skipped_without_a_url(monkeypatch):
    clear_env(monkeypatch)
    assert notify_mod.send_webhook("件名", "本文") is False


def test_mail_is_skipped_without_full_settings(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("PAYSTOCK_SMTP_HOST", "smtp.example.com")
    # ユーザ名やパスワードが欠けている状態では送信を試みない
    assert notify_mod.send_mail("件名", "本文") is False


def test_notify_reports_nothing_when_unconfigured(monkeypatch):
    clear_env(monkeypatch)
    assert notify_mod.notify("件名", "本文") == []


def test_slack_payload_uses_text_field():
    payload = notify_mod._payload_for("https://hooks.slack.com/services/x", "件名", "本文")

    assert "text" in payload
    assert "件名" in payload["text"]


def test_discord_payload_uses_content_field():
    payload = notify_mod._payload_for("https://discord.com/api/webhooks/x", "件名", "本文")

    assert "content" in payload


def test_discord_payload_is_truncated():
    """Discord は 2000 文字を超えると 400 を返すので、こちらで切る。"""
    payload = notify_mod._payload_for("https://discord.com/api/webhooks/x", "件名", "あ" * 5000)

    assert len(payload["content"]) <= 1900


def test_notify_keeps_going_when_one_channel_fails(monkeypatch):
    """Webhook が落ちてもメールは試す。1つも届かないより1つ届くほうがよい。"""
    clear_env(monkeypatch)

    def broken(subject, body, url=None):
        raise notify_mod.NotifyError("接続失敗")

    monkeypatch.setattr(notify_mod, "send_webhook", broken)
    monkeypatch.setattr(notify_mod, "send_mail", lambda s, b: True)

    assert notify_mod.notify("件名", "本文") == ["mail"]


def test_notify_swallows_errors_rather_than_crashing(monkeypatch):
    """通知の失敗で分析そのものを落とさない。"""
    clear_env(monkeypatch)

    def broken(*args, **kwargs):
        raise notify_mod.NotifyError("失敗")

    monkeypatch.setattr(notify_mod, "send_webhook", broken)
    monkeypatch.setattr(notify_mod, "send_mail", broken)

    assert notify_mod.notify("件名", "本文") == []
