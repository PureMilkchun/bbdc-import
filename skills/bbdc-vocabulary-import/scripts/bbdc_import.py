#!/usr/bin/env python3
"""Prepare, check and import vocabulary through the user's own BBDC web session."""

from __future__ import annotations

import argparse
from contextlib import suppress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv

import bbdc_remote as remote
from bbdc_words import InputError, WordList, read_terms, save_remaining, write_csv


VERSION = "1.1.0"
WORD_GAP = 0.4
PREVIEW_SIZE = 8
AUTH_ERRORS = {"login_required", "login_timeout", "login_cancelled"}
HINTS = {
    "invalid_rows": "按行号修正词表后再试；这次没有上传任何词。",
    "invalid_term": "每行一个英文词或短语，可包含连字符或撇号。把释义和例句放到其他列。",
    "invalid_header": "把要导入的那一列命名为 word、lemma 或「单词」，只保留一个词列。",
    "invalid_columns": "让这行列数和表头一致；含分隔符的字段请加英文双引号。",
    "invalid_flags": "WordRadar 的 flags 列用非负整数；0 表示待推。",
    "invalid_csv": "重新导出 CSV/TSV，检查这个位置附近的引号和分隔符。",
    "input_too_large": "输入文件超过 5 MB。请只保留词列，或分成几份。",
    "invalid_encoding": "请另存为 UTF-8；也支持带 BOM 的 UTF-16。",
    "unreadable_input": "打开词表路径失败。确认文件存在、可读取，并给含空格的路径加引号。",
    "output_exists": "输出文件已存在。请换一个文件名，原文件未修改。",
    "output_unwritable": "无法写入输出文件。确认目录存在且可写，再换路径重试。",
    "wordlist_changed": "词表与预览不一致。重新运行 prepare，核对后再推送。",
    "login_required": "当前没有有效登录。可以运行 login，或给 push / check 加 --login，在弹出的窗口里登录。",
    "login_timeout": "等待登录超时。准备好后重试登录，或用 --login-timeout 延长等待。",
    "login_cancelled": "登录窗口已关闭。准备好后重新打开登录窗口。",
    "login_page_unavailable": "不背单词的登录页没有打开。检查网络，或先用 CSV 导入 WordRadar。",
    "access_denied": "不背单词拒绝了访问。先在网页检查账号和网络；若仍失败，可用 WordRadar 导入 CSV。",
    "rate_limited": "服务暂时限制了请求。等恢复后再推送剩余词，不要连续重试。",
    "write_unconfirmed": "当前这个词可能已写入。稍后用 check 核对，或重跑词表让脚本先查重。",
    "network_error": "连接中断。确认网络可用后重推剩余词。",
    "unexpected_redirect": "接口返回意外跳转。先检查网页能否打开；必要时改用 WordRadar。",
    "invalid_response": "服务返回了无法识别的数据。暂停推送，先检查服务，或导出 CSV 给 WordRadar。",
    "wordbook_changed": "读取时生词本发生变化，已暂停。请稍后重新核对。",
    "word_rejected": "不背单词没有接受这个词。核对拼写；如果是短语，试一下单词形式。",
    "repeated_rejection": "连续几个词都被不背单词拒绝。先核对词表和网页，再决定是否重推。",
    "profile_busy": "这个浏览器会话正在使用中。等另一轮结束或关闭账号窗口后再试。",
    "runtime_busy": "另一个进程正在配置运行环境。等它结束后再试。",
    "missing_playwright": "当前 Python 缺少 Playwright。运行 setup，再使用它返回的 Python 路径。",
    "playwright_version": "当前 Playwright 版本不兼容。运行 setup，再使用它返回的 Python 路径。",
    "missing_browser": "找不到所选浏览器。默认可运行 setup，也可明确选用已安装的 Edge 或 Chrome。",
    "missing_system_libraries": "Chromium 缺少系统运行库。请按安装说明补齐，或者使用 WordRadar。",
    "display_unavailable": "这个运行环境打不开登录窗口。请在有桌面的本机运行，或者用 WordRadar。",
    "browser_unavailable": "无法启动或连接到浏览器。先用 doctor 检查，必要时用 WordRadar。",
    "setup_failed": "自动配置没有完成。查看上方安装日志，或按安装说明手动配置。",
    "setup_timeout": "配置运行环境超时。检查网络后重试，或选用已安装的 Edge 或 Chrome。",
    "local_io_error": "无法读写本机的工作目录。检查目录权限和磁盘空间后再试。",
    "recovery_not_trimmed": "未能更新剩余词表。保留的是完整备份，之后重跑时会先查重。",
    "interrupted": "已停止。需要继续时使用剩余词表；若没有，使用原词表重新查重。",
}


def tell(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def hint_for(code: str) -> str:
    return HINTS.get(code, "服务请求没有完成。稍后检查连接和词表，再推送剩余的词。")


def describe(report: dict, code: str, **details) -> None:
    report.update(details)
    report["hint"] = hint_for(code)
    tell(report["hint"])
    if seconds := report.get("retry_after_seconds"):
        tell(f"服务建议至少等待 {seconds} 秒。")


def setup(browser: str, tell_user) -> str:
    try:
        with remote.open_browser(browser, use_profile=False):
            return sys.executable
    except remote.ServiceError as error:
        if error.code not in {"missing_browser", "missing_playwright", "playwright_version"}:
            raise

    runtime = remote.data_dir() / "runtime"
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PYTHONUTF8": "1", "NODE_TLS_REJECT_UNAUTHORIZED": "1"}

    def run(*parts: str) -> None:
        try:
            subprocess.run(
                [str(python), *parts], check=True, stdout=sys.stderr, env=env, timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise remote.ServiceError("setup_timeout", "setup") from None
        except subprocess.CalledProcessError:
            raise remote.ServiceError("setup_failed", "setup") from None

    with remote.exclusive(runtime.with_suffix(".lock"), "runtime_busy", "setup"):
        if not python.is_file() or not (runtime / "pyvenv.cfg").is_file():
            tell_user("正在创建独立的 Python 运行环境。")
            try:
                venv.EnvBuilder(with_pip=True).create(runtime)
            except (OSError, subprocess.SubprocessError):
                raise remote.ServiceError("setup_failed", "setup") from None
        tell_user("正在检查 Playwright 依赖。")
        run("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--quiet",
            "-r", str(Path(__file__).with_name("requirements.txt")))
        if browser == "chromium":
            tell_user("正在检查 Chromium 浏览器，首次可能需要下载。")
            run("-m", "playwright", "install", "--no-shell", "chromium")
        # Report the actual browser diagnostic without making a BBDC request.
        tell_user("正在检查浏览器能否启动。")
        try:
            result = subprocess.run(
                [str(python), str(Path(__file__).resolve()), "doctor", "--browser", browser],
                stdout=subprocess.PIPE, stderr=sys.stderr, env=env, text=True, encoding="utf-8", timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise remote.ServiceError("setup_timeout", "setup") from None
        if result.returncode:
            try:
                code = json.loads(result.stdout)["stop_reason"]
            except (ValueError, KeyError, TypeError):
                code = "setup_failed"
            raise remote.ServiceError(code, "setup")
    return str(python)


def run_batch(
    terms: list[str], account, dictionary, outcomes: dict[str, dict], *, lookup_only: bool,
    tell_user=tell,
) -> None:
    verb = "核对" if lookup_only else "推送"
    tell_user(f"开始{verb} {len(terms)} 个词。")
    existing_words = remote.list_wordbook(account)
    last_update = time.monotonic()
    rejection_streak = 0
    last_rejection = None
    for index, word in enumerate(terms, 1):
        if index > 1:
            time.sleep(WORD_GAP)
        stage = "check_existing"
        try:
            if word in existing_words:
                outcomes[word] = {"state": "existing"}
            elif lookup_only:
                outcomes[word] = {"state": "missing"}
            else:
                stage = "definition"
                info = remote.definition(dictionary, word)
                stage = "add"
                remote.add_word(account, word, info)
                outcomes[word] = {"state": "added", "without_definition": not info}
                existing_words.add(word)
                rejection_streak = 0
        except remote.ServiceError as error:
            state = "uncertain" if error.code == "write_unconfirmed" else "failed"
            outcomes[word] = {"state": state, **error.details()}
            if error.code != "word_rejected":
                raise
            rejection_streak = rejection_streak + 1 if error.remote_code == last_rejection else 1
            last_rejection = error.remote_code
            if rejection_streak >= 3:
                raise remote.ServiceError("repeated_rejection", "add", remote_code=last_rejection) from None
        except KeyboardInterrupt:
            if stage == "add" and word not in outcomes:
                outcomes[word] = {"state": "uncertain", "stage": "add", "code": "write_unconfirmed"}
            raise
        now = time.monotonic()
        if index == len(terms) or now - last_update >= 3:
            tell_user(f"已{verb} {index}/{len(terms)} 个词。")
            last_update = now


def run_remote(args, terms: list[str], report: dict, outcomes: dict[str, dict]) -> None:
    if args.command == "setup":
        report.update(status="ready", python=setup(args.browser, tell))
        tell("环境已就绪。")
        return
    if args.command == "doctor":
        with remote.open_browser(args.browser, use_profile=False):
            report.update(status="ready", python=sys.executable)
            tell("浏览器和依赖可以运行。")
        return

    wait = args.login_timeout if args.command == "login" or getattr(args, "login", False) else None
    with remote.open_browser(args.browser, visible=wait is not None or args.command == "account") as (pw, context):
        if args.command == "account":
            from playwright.sync_api import Error as PlaywrightError

            tell("在窗口里查看或切换账号。关闭窗口后回到这里；这个命令不会推送生词。")
            remote.open_account_page(context, tell)
            try:
                context.wait_for_event("close", timeout=0)
            except PlaywrightError:
                if context.pages:
                    raise
            report["status"] = "closed"
            return
        remote.ensure_login(context, wait, tell)
        report["logged_in"] = True
        if args.command == "check":
            run_batch(terms, context.request, None, outcomes, lookup_only=True)
        elif args.command == "push":
            # The dictionary receives only the word, through its own cookie jar.
            from playwright.sync_api import Error as PlaywrightError

            dictionary = pw.request.new_context()
            try:
                run_batch(terms, context.request, dictionary, outcomes, lookup_only=False)
            finally:
                with suppress(PlaywrightError):
                    dictionary.dispose()
        else:
            report["status"] = "signed_in"
            tell("当前会话已登录。")


def wait_seconds(value: str) -> int:
    seconds = int(value)
    if not 10 <= seconds <= 1800:
        raise argparse.ArgumentTypeError("等待时间须在 10 到 1800 秒之间")
    return seconds


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="把英文词表导入不背单词的「我的生词本」。")
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="离线检查词表，可导出 WordRadar CSV")
    prepare.add_argument("file", type=Path)
    prepare.add_argument("--out", type=Path, help="保存为新 CSV，不覆盖已有文件")
    for name, help_text in (
        ("push", "向已登录的账号逐词推送"),
        ("check", "只核对哪些词已经在生词本里"),
        ("login", "登录账号，不推送"),
        ("status", "检查登录状态，不打开窗口"),
        ("account", "打开账号窗口，方便查看或切换账号"),
        ("doctor", "检查本机依赖，不访问不背单词"),
        ("setup", "检查或配置本机依赖，不访问不背单词"),
    ):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--browser", choices=remote.CHANNELS, default="chromium",
                         help="默认 chromium；edge 和 chrome 使用独立的会话")
        if name in {"push", "check"}:
            cmd.add_argument("file", type=Path)
            cmd.add_argument("--login", action="store_true", help="打开交互浏览器让你登录")
            cmd.add_argument("--expect", metavar="SHA256", help="只处理与 prepare 指纹相同的词表")
        if name in {"push", "check", "login"}:
            cmd.add_argument("--login-timeout", type=wait_seconds, default=300, metavar="SECONDS",
                             help="等待手动登录的秒数，默认 300")
        if name == "push":
            cmd.add_argument("--remaining-out", type=Path,
                             help="开始前保存一份新 CSV，结束后只保留未完成的词")
    return root


def input_list(args, report: dict) -> WordList:
    try:
        batch = read_terms(args.file)
    except UnicodeError:
        raise InputError("invalid_encoding") from None
    except OSError:
        raise InputError("unreadable_input") from None
    report.update(total=len(batch.words), duplicates=batch.duplicates, skipped_by_csv_flag=batch.flagged,
                  digest=batch.digest, preview=batch.words[:PREVIEW_SIZE])
    if (expected := getattr(args, "expect", None)) is not None and expected.lower() != batch.digest:
        raise InputError("wordlist_changed")
    return batch


def create_csv(path: Path, words: list[str]) -> None:
    try:
        write_csv(path, words)
    except FileExistsError:
        raise InputError("output_exists") from None
    except OSError:
        raise InputError("output_unwritable") from None


def summarize(report: dict, outcomes: dict[str, dict], total: int, running_push: bool) -> None:
    def count(state: str) -> int:
        return sum(result["state"] == state for result in outcomes.values())

    def items(state: str) -> list[dict]:
        return [{**{k: v for k, v in result.items() if k != "state"}, "word": word}
                for word, result in outcomes.items() if result["state"] == state]

    report.update(existing=count("existing"), failed=items("failed"), unattempted=total - len(outcomes))
    if running_push:
        report.update(added=count("added"), uncertain=items("uncertain"),
                      added_without_definition=sum(bool(result.get("without_definition")) for result in outcomes.values()))
    else:
        report.update(missing=count("missing"),
                      missing_preview=[word for word, result in outcomes.items() if result["state"] == "missing"][:PREVIEW_SIZE])


def emit(report: dict) -> None:
    # Escaped Unicode makes stdout valid JSON even with a legacy terminal code page.
    print(json.dumps(report))


def main() -> int:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args()
    report: dict = {"command": args.command}
    if hasattr(args, "browser"):
        report["browser"] = args.browser
    batch = None
    recovery = None
    outcomes: dict[str, dict] = {}
    try:
        if args.command in {"prepare", "push", "check"}:
            batch = input_list(args, report)
        if args.command == "prepare" and args.out is not None:
            create_csv(args.out, batch.words)
            report["csv"] = str(args.out)
        if args.command == "push" and args.remaining_out is not None:
            create_csv(args.remaining_out, batch.words)
            recovery = args.remaining_out
            report["remaining_csv"] = str(recovery)
    except InputError as error:
        report.update(status="invalid_input", error=error.code, issues=error.issues, issue_count=error.count)
        describe(report, error.code)
        for issue in error.issues:
            tell(f"第 {issue['line']} 行：{hint_for(issue['code'])}")
        emit(report)
        return 2

    terms = batch.words if batch is not None else []
    if batch is not None and not terms:
        report["status"] = "nothing_to_do"
        report["hint"] = ("文件中的词都标记为已推送。" if batch.flagged
                          else "没有可处理的词。请在词表中添加要处理的词。")
        tell(report["hint"])
    elif args.command == "prepare":
        report["status"] = "prepared"
        tell(f"发现 {len(terms)} 个待处理的词，已去重 {batch.duplicates} 项，跳过已推送 {batch.flagged} 项。")
    else:
        try:
            run_remote(args, terms, report, outcomes)
        except remote.ServiceError as error:
            details = error.details()
            describe(report, details.pop("code"), stop_reason=error.code, **details)
        except OSError:
            describe(report, "local_io_error", stop_reason="local_io_error")
        except KeyboardInterrupt:
            describe(report, "interrupted", stop_reason="interrupted")

    if args.command in {"push", "check"}:
        summarize(report, outcomes, len(terms), args.command == "push")
        if recovery is not None:
            remaining = [word for word in terms if outcomes.get(word, {}).get("state") not in {"added", "existing"}]
            # If every word is outstanding, the initial backup is already correct.
            if len(remaining) != len(terms):
                try:
                    save_remaining(recovery, remaining)
                except (OSError, KeyboardInterrupt):
                    report["warning"] = "recovery_not_trimmed"
                    tell(hint_for(report["warning"]))
        if "status" not in report:
            report["status"] = ("paused" if report.get("stop_reason") else "partial" if report["failed"]
                                else "checked" if args.command == "check" else "completed")
        if report["status"] not in {"nothing_to_do"}:
            if args.command == "push":
                tell(f"新增 {report['added']}，已存在 {report['existing']}，失败 {len(report['failed'])}，"
                     f"待核对 {len(report['uncertain'])}，未尝试 {report['unattempted']}。")
            else:
                tell(f"已存在 {report['existing']}，未找到 {report['missing']}，"
                     f"未完成核对 {report['unattempted'] + len(report['failed'])}。")
    elif "status" not in report:
        report["status"] = "stopped"

    emit(report)
    if report.get("stop_reason") in AUTH_ERRORS:
        return 3
    return 1 if report.get("stop_reason") or report.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
