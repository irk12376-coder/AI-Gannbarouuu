"""Codex から Claude Code へ実装を委託するローカルランナー。

ローカル環境限定の MVP。外部サーバー・GitHub・API キーは一切使わず、
すでにログイン済みの Claude Code CLI をそのまま利用する。

  python -m claude_delegate.cli check
  python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --dry-run
  python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --approve
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
