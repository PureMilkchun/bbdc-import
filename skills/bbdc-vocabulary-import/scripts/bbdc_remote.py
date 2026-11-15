"""BBDC protocol and the dedicated browser session. Never reads cookie values."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import errno
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import sys
import time


BBDC = "https://bbdc.cn"
DICTIONARY = "https://langeasy.com.cn"
CHANNELS = {"chromium": "chromium", "edge": "msedge", "chrome": "chrome"}
LOGIN_WORDS = {"请登录", "请先登录", "未登录", "登录已过期", "请重新登录"}
REQUEST_TIMEOUT_MS = 12_000


class ServiceError(Exception):
    def __init__(
        self, code: str, stage: str, *, remote_code: int | None = None, retry_after: int | None = None
    ):
        super().__init__(f"{stage}: {code}")
        self.code = code
        self.stage = stage
        self.remote_code = remote_code
        self.retry_after = retry_after

    def details(self) -> dict:
        return {
            key: value for key, value in {
                "code": self.code, "stage": self.stage,
                "remote_code": self.remote_code, "retry_after_seconds": self.retry_after,
            }.items() if value is not None
        }


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        candidate = Path(os.environ.get("XDG_DATA_HOME", ""))
        base = candidate if candidate.is_absolute() else Path.home() / ".local" / "share"
    return base / "bbdc-vocabulary-import"


@contextmanager
def exclusive(path: Path, code: str, stage: str):
    """Use an OS lock so a killed process cannot leave the Skill locked out."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ServiceError(code, stage) from None
            raise
        yield


def result_code(body: dict) -> int | None:
    code = body.get("result_code", body.get("resultCode"))
    return code if type(code) is int else None


def login_message(body: dict) -> bool:
    error = body.get("error_body")
    return isinstance(error, dict) and any(
        isinstance(value, str) and value.strip().rstrip("。！!.") in LOGIN_WORDS
        for value in (error.get("user_message"), error.get("info"))
    )


def retry_after_seconds(header: str | None) -> int | None:
    if header is None:
        return None
    try:
        if header.isascii() and header.isdigit() and len(header) < 10:
            return int(header)
        moment = parsedate_to_datetime(header)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((moment - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, OverflowError):
        return None


def fetch_once(request, url: str, stage: str, params, form, timeout_ms: int) -> dict:
    from playwright.sync_api import Error as PlaywrightError

    try:
        options = {"timeout": timeout_ms, "max_redirects": 0}
        if form is None:
            response = request.get(url, params=params, **options)
        else:
            response = request.post(url, multipart=form, **options)
    except PlaywrightError:
        raise ServiceError("network_error", stage) from None
    try:
        status = response.status
        if status == 429:
            raise ServiceError(
                "rate_limited", stage, remote_code=status,
                retry_after=retry_after_seconds(response.headers.get("retry-after")),
            )
        account = stage != "definition"
        if account and status == 401:
            raise ServiceError("login_required", stage, remote_code=status)
        if not response.ok and not (account and status == 403):
            code = "unexpected_redirect" if 300 <= status < 400 else f"http_{status}"
            raise ServiceError(code, stage, remote_code=status)
        try:
            body = response.json()
        except (ValueError, PlaywrightError):
            body = None
        if not isinstance(body, dict):
            if status == 403:
                raise ServiceError("access_denied", stage, remote_code=status)
            raise ServiceError("invalid_response", stage)
        remote = result_code(body)
        if account and (remote == 401 or login_message(body)):
            raise ServiceError("login_required", stage, remote_code=remote)
        if remote == 429:
            raise ServiceError("rate_limited", stage, remote_code=remote)
        if status == 403 or (account and remote == 403):
            raise ServiceError("access_denied", stage, remote_code=403)
        return body
    finally:
        with suppress(PlaywrightError):
            response.dispose()


def fetch_json(
    request, url: str, stage: str, *, params=None, form=None, timeout_ms: int = REQUEST_TIMEOUT_MS,
    retry_reads: bool = True,
) -> dict:
    # At most two GET attempts; never automatically repeat a POST or a 429.
    try:
        return fetch_once(request, url, stage, params, form, timeout_ms)
    except ServiceError as error:
        transient = error.code in {"network_error", "http_408"} or error.code.startswith("http_5")
        if form is not None or not retry_reads or not transient:
            raise
    time.sleep(0.8)
    return fetch_once(request, url, stage, params, form, timeout_ms)


def check_login(request, *, timeout_ms: int = REQUEST_TIMEOUT_MS, retry_reads: bool = True) -> None:
    body = fetch_json(
        request, f"{BBDC}/api/check-login", "login", timeout_ms=timeout_ms, retry_reads=retry_reads
    )
    code = result_code(body)
    if code != 200:
        # The site's explicit logged-out response is handled by login_message().
        raise ServiceError("invalid_response", "login", remote_code=code)


def open_account_page(context, tell):
    from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(f"{BBDC}/", wait_until="domcontentloaded", timeout=20_000)
    except PlaywrightTimeout:
        if not page.url.startswith(f"{BBDC}/"):
            raise ServiceError("login_page_unavailable", "login") from None
        tell("网页加载较慢，等页面显示后再登录。")
    except PlaywrightError:
        code = "login_cancelled" if page.is_closed() else "login_page_unavailable"
        raise ServiceError(code, "login") from None
    return page


def ensure_login(context, login_timeout: int | None, tell) -> None:
    try:
        check_login(context.request)
        return
    except ServiceError as error:
        if login_timeout is None or error.code != "login_required":
            raise

    tell(f"在打开的窗口里登录不背单词，登录后保持窗口打开。最多等待 {login_timeout} 秒。")
    open_account_page(context, tell)
    deadline = time.monotonic() + login_timeout
    while context.pages and (remaining := deadline - time.monotonic()) > 0:
        try:
            check_login(
                context.request, timeout_ms=max(1, min(REQUEST_TIMEOUT_MS, int(remaining * 1000))),
                retry_reads=False,
            )
            tell("已登录，正在使用这个账号。")
            return
        except ServiceError as error:
            if error.code not in {"login_required", "network_error"}:
                raise
        time.sleep(max(0, min(2.5, deadline - time.monotonic())))
    code = "login_timeout" if context.pages else "login_cancelled"
    raise ServiceError(code, "login")


def exists(request, word: str) -> bool:
    body = fetch_json(request, f"{BBDC}/api/check-new-word", "check_existing",
                      params={"word": word, "infoidx": "100"})
    code = result_code(body)
    data = body.get("data_body")
    if (code not in (None, 200) or not isinstance(data, dict)
            or not isinstance(data.get("list", []), list)):
        raise ServiceError("invalid_response", "check_existing", remote_code=code)
    return bool(data.get("list", []))


def definition(request, word: str) -> str:
    body = fetch_json(request, f"{DICTIONARY}/loadLexisList.action", "definition",
                      params={"strict": "1", "word": word})
    matches = body.get("wordlist")
    if matches == []:
        return ""
    if (not isinstance(matches, list) or not isinstance(matches[0], dict)
            or not isinstance(info := matches[0].get("interpret"), str)):
        raise ServiceError("invalid_response", "definition")
    return info.strip()


def add_word(request, word: str, info: str) -> None:
    fields = {"word": word, "info": info, "course": "*", "wordidx": "*",
              "infoidx": "100", "selection": "*", "opcode": "1"}
    try:
        body = fetch_json(request, f"{BBDC}/api/user-new-word", "add",
                          form={"newwordlist": json.dumps(fields, ensure_ascii=False)})
    except ServiceError as error:
        if (error.code in {"network_error", "invalid_response", "unexpected_redirect", "http_408"}
                or error.code.startswith("http_5")):
            raise ServiceError("write_unconfirmed", "add", remote_code=error.remote_code) from None
        raise
    code = result_code(body)
    if code != 200:
        # 20000 is an upstream generic error, not proof that nothing was written.
        reason = "write_unconfirmed" if code in (None, 20000) else "word_rejected"
        raise ServiceError(reason, "add", remote_code=code)


@contextmanager
def open_browser(browser: str, *, visible: bool = False, use_profile: bool = True):
    try:
        installed = version("playwright")
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except (ImportError, PackageNotFoundError):
        raise ServiceError("missing_playwright", "setup") from None
    major, _, release = installed.partition(".")
    minor = release.partition(".")[0]
    if major != "1" or not minor.isdigit() or int(minor) < 58:
        raise ServiceError("playwright_version", "setup")
    # The driver must verify HTTPS even if the host provides a permissive Node setting.
    old_tls = os.environ.get("NODE_TLS_REJECT_UNAUTHORIZED")
    os.environ["NODE_TLS_REJECT_UNAUTHORIZED"] = "1"
    try:
        with sync_playwright() as playwright:
            options = {"channel": CHANNELS[browser], "headless": not visible, "timeout": 25_000}
            if not use_profile:
                instance = playwright.chromium.launch(**options)
                with suppress(PlaywrightError):
                    instance.close()
                yield playwright, None
            else:
                profile = data_dir() / browser
                with exclusive(profile.with_suffix(".lock"), "profile_busy", "browser"):
                    profile.mkdir(mode=0o700, exist_ok=True)
                    context = playwright.chromium.launch_persistent_context(str(profile), **options)
                    try:
                        yield playwright, context
                    finally:
                        with suppress(PlaywrightError):
                            context.close()
    except PlaywrightError as error:
        text = str(error).lower()
        if "executable doesn't exist" in text or "distribution" in text and "not found" in text:
            code = "missing_browser"
        elif "missing dependencies" in text or "missing libraries" in text or "error while loading shared libraries" in text:
            code = "missing_system_libraries"
        elif "missing x server" in text or "without having a xserver" in text:
            code = "display_unavailable"
        elif "singletonlock" in text or "process singleton" in text or "profile in use" in text:
            code = "profile_busy"
        else:
            code = "browser_unavailable"
        raise ServiceError(code, "browser") from None
    finally:
        if old_tls is None:
            os.environ.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
        else:
            os.environ["NODE_TLS_REJECT_UNAUTHORIZED"] = old_tls
