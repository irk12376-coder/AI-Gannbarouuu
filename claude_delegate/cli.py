"""Codex から Claude Code へ実装を委託するローカルランナーの CLI。

  python -m claude_delegate.cli check
  python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --dry-run
  python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --approve

終了コード: 0 = 成功 / 1 = 実行失敗 / 2 = 中止 (安全弁・引数不備)
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

from . import __version__, runner, safety
from .safety import SafetyError

#: claude が見つからない dry-run で仮定するオプション群
ASSUMED_FLAGS = {
    "--output-format",
    "--permission-mode",
    "--tools",
    "--allowedTools",
    "--disallowedTools",
    "--model",
}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ABORTED = 2


def _print(*args: object) -> None:
    print(*args)


def _section(title: str) -> None:
    _print(f"\n== {title} ==")


#: `claude auth status` の JSON から表示してよいキーだけを抜き出す
_AUTH_STATUS_SAFE_KEYS = ("loggedIn", "authMethod", "apiProvider")


def _summarize_auth_status(masked_output: str) -> str:
    """`claude auth status` の出力を 1 行に要約する。値そのもの (トークン等) は出さない。"""
    text = masked_output.strip()
    if not text:
        return "(出力なし)"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.splitlines()[0]
    if isinstance(data, dict):
        pairs = [f"{k}={data[k]}" for k in _AUTH_STATUS_SAFE_KEYS if k in data]
        if pairs:
            return ", ".join(pairs)
    return text.splitlines()[0]


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    """実行環境を確認する。認証情報そのものは絶対に表示しない。"""
    ok = True

    _section("Python")
    _print(f"  実行中のPython : {platform.python_version()} ({sys.executable})")
    if sys.version_info < (3, 11):
        _print("  [NG] Python 3.11 以上が必要です")
        ok = False

    _section("Claude Code CLI")
    executable = shutil.which("claude")
    if not executable:
        _print("  [NG] claude コマンドが見つかりません")
        _print("       Claude Code CLI を導入し `claude auth login` を済ませてください")
        ok = False
    else:
        _print(f"  claude         : {executable}")
        code, out = runner.probe([executable, "--version"])
        out = safety.mask_secrets(out)
        _print(f"  --version      : {out or '(出力なし)'}")
        if code != 0:
            _print("  [NG] claude --version が失敗しました")
            ok = False

        code, out = runner.probe([executable, "auth", "status"])
        out = safety.mask_secrets(out)
        _print(f"  auth status    : {_summarize_auth_status(out)}")
        if code == 0:
            _print("  ログイン状態   : OK (既存のログインを利用します)")
        else:
            _print("  [NG] ログインしていない可能性があります (`claude auth login`)")
            ok = False

        flags = runner.detect_flags(executable)
        available = [f for f in ("--output-format", "--permission-mode", "--tools",
                                 "--allowedTools", "--disallowedTools") if f in flags]
        missing = [f for f in ("--tools", "--allowedTools") if f not in flags]
        _print(f"  使えるオプション: {', '.join(available) or '(検出できず)'}")
        if missing:
            _print(f"  [注意] {', '.join(missing)} が無いため、ツール制限は弱くなります")

    _section("対象プロジェクト")
    try:
        project_dir = safety.resolve_project_dir(args.project_dir)
    except SafetyError as exc:
        _print(f"  [NG] {exc}")
        return EXIT_ABORTED
    _print(f"  絶対パス       : {project_dir}")
    _print(f"  ログ保存先     : {project_dir / runner.LOG_DIRNAME}")

    _section("Git")
    if not shutil.which("git"):
        _print("  [注意] git コマンドがありません (変更ファイルの一覧は出せません)")
    elif not runner.is_git_repo(project_dir):
        _print("  [注意] Git リポジトリではありません")
    else:
        changed = runner.changed_files(project_dir)
        _print(f"  リポジトリ     : はい")
        if changed:
            _print(f"  未コミット変更 : {len(changed)} 件")
            for line in changed[:20]:
                _print(f"    {line}")
            if len(changed) > 20:
                _print(f"    ... 他 {len(changed) - 20} 件")
            _print("  [注意] 委託中は Codex 側でこれらのファイルを触らないでください")
        else:
            _print("  未コミット変更 : なし (クリーン)")

    lock = project_dir / safety.LOCK_FILENAME
    if lock.exists():
        _print(f"\n  [注意] ロックが残っています: {lock}")

    _print("\n" + ("結果: 実行できます" if ok else "結果: 上の [NG] を解消してください"))
    return EXIT_OK if ok else EXIT_ABORTED


# ---------------------------------------------------------------------------
# run / dry-run
# ---------------------------------------------------------------------------

def _resolve_target(args: argparse.Namespace) -> tuple[Path, Path]:
    """プロジェクトとタスクファイルを解決する。外部パスはここで弾く。"""
    project_dir = safety.resolve_project_dir(args.project_dir)
    task_path = safety.ensure_inside(args.task, project_dir, "タスクファイル")
    return project_dir, task_path


def _executable_and_flags(dry_run: bool) -> tuple[str, set[str]]:
    """使う claude と、そのバージョンで使えるオプションを決める。"""
    if dry_run:
        found = shutil.which("claude")
        if not found:
            _print("[注意] claude コマンドが見つかりません。"
                   "dry-run なので想定オプションで計画だけ表示します。")
            return "claude", set(ASSUMED_FLAGS)
        return found, runner.detect_flags(found)
    executable = runner.find_claude()
    return executable, runner.detect_flags(executable)


def _show_plan(plan: runner.Plan, *, dry_run: bool) -> None:
    header = "dry-run: 実行内容の確認 (Claude Code は起動しません)" if dry_run \
        else "実行内容"
    _section(header)
    _print(f"  作業フォルダ   : {plan.project_dir}")
    _print(f"  タスクファイル : {plan.task_path}")
    _print(f"  テストコマンド : {plan.test_command}")
    _print(f"  タイムアウト   : {plan.timeout_sec} 秒")
    _print(f"  ログ保存先     : {plan.project_dir / runner.LOG_DIRNAME}")

    _section("許可するツール / コマンド")
    for rule in plan.allowed_tools:
        _print(f"  + {rule}")
    _section("拒否するツール / コマンド")
    for rule in plan.disallowed_tools:
        _print(f"  - {rule}")

    _section("起動コマンド")
    for arg in plan.safe_argv():
        _print(f"  {arg}")

    _section("Claude Code へ渡す指示")
    for line in plan.prompt.splitlines():
        _print(f"  | {line}")

    notes = [f for f in plan.findings if not f.blocking]
    if notes:
        _section("注意した記述 (禁止事項の記載として扱い、続行します)")
        for finding in notes:
            _print("  " + finding.format())


def _show_result(plan: runner.Plan, result: runner.RunResult,
                 changed: list[str], tests: list[str], log_path: Path) -> None:
    _section("Claude Code の報告")
    text = safety.mask_secrets(result.result_text).strip()
    _print(text or "  (出力なし)")

    if result.stderr.strip():
        _section("stderr")
        _print(safety.mask_secrets(result.stderr).strip()[:4000])

    _section("変更ファイル")
    if changed:
        for line in changed:
            _print(f"  {line}")
    else:
        _print("  (git 上の変更はありません)")

    _section("テスト結果")
    if tests:
        for line in tests:
            _print(f"  {line}")
    else:
        _print("  (報告からテスト結果を検出できませんでした。上の報告を確認してください)")

    _section("実行サマリ")
    _print(f"  状態           : {result.status}")
    _print(f"  終了コード     : {result.returncode}")
    _print(f"  所要時間       : {result.duration_sec:.1f} 秒")
    _print(f"  ログ           : {log_path}")


def cmd_run(args: argparse.Namespace) -> int:
    if args.dry_run and args.approve:
        _print("[注意] --dry-run と --approve の両方が指定されました。dry-run を優先します。")
        args.approve = False

    project_dir, task_path = _resolve_target(args)
    executable, flags = _executable_and_flags(args.dry_run)

    plan = runner.build_plan(
        project_dir,
        task_path,
        test_command=args.test_command,
        timeout_sec=args.timeout,
        model=args.model,
        executable=executable,
        flags=flags,
    )
    _show_plan(plan, dry_run=args.dry_run)

    if args.dry_run:
        _print("\ndry-run のため Claude Code は起動していません。")
        _print("実行するには --dry-run を外し --approve を付けてください。")
        return EXIT_OK

    if not args.approve:
        _print("\n[中止] --approve が無いので Claude Code は起動しません。")
        _print("       内容を確認するには --dry-run を付けてください。")
        return EXIT_ABORTED

    with safety.ProjectLock(project_dir):
        _print("\nClaude Code を起動します (最大 "
               f"{plan.timeout_sec} 秒)。実行中はこのフォルダを編集しないでください。")
        result = runner.execute(plan)
        changed = runner.changed_files(project_dir)
        tests = runner.summarize_tests(
            f"{result.result_text}\n{result.stdout}\n{result.stderr}"
        )
        log_path = runner.write_log(plan, result, changed=changed, tests=tests)

    _show_result(plan, result, changed, tests, log_path)

    if result.status == "timeout":
        _print("\n[失敗] タイムアウトしました。作業ツリーの状態を確認してください。")
        return EXIT_FAILED
    if not result.ok:
        _print("\n[失敗] Claude Code が異常終了しました。ログを確認してください。")
        return EXIT_FAILED
    _print("\n[成功] 委託は完了しました。差分を必ずレビューしてください。")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 引数
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m claude_delegate.cli",
        description="Codex が作った実装指示を Claude Code CLI へ委託するローカルランナー",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="実行環境とログイン状態を確認する")
    check.add_argument("--project-dir", default=".", help="対象プロジェクト (既定: .)")
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="タスクを Claude Code へ委託する")
    run.add_argument("--task", required=True, help="タスクファイル (プロジェクト内)")
    run.add_argument("--project-dir", default=".", help="対象プロジェクト (既定: .)")
    run.add_argument("--dry-run", action="store_true",
                     help="実行内容を表示するだけで Claude Code を起動しない")
    run.add_argument("--approve", action="store_true",
                     help="実際に Claude Code を起動する (無いと起動しない)")
    run.add_argument("--timeout", type=int, default=safety.DEFAULT_TIMEOUT_SEC,
                     help=f"実行時間の上限 (秒、既定: {safety.DEFAULT_TIMEOUT_SEC})")
    run.add_argument("--test-command", default=runner.DEFAULT_TEST_COMMAND,
                     help=f"Claude Code に実行させるテスト (既定: {runner.DEFAULT_TEST_COMMAND})")
    run.add_argument("--model", default=None, help="使うモデル (省略時は既定)")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SafetyError as exc:
        _print(f"\n[中止] {exc}")
        return EXIT_ABORTED
    except KeyboardInterrupt:
        _print("\n[中止] 中断されました。")
        return EXIT_ABORTED


if __name__ == "__main__":
    raise SystemExit(main())
