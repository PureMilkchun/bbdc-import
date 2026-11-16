"""Local regression tests. Run with python -B -m unittest discover -s tests -v."""

from contextlib import redirect_stderr, redirect_stdout
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "bbdc-vocabulary-import" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bbdc_import as cli
import bbdc_remote as remote
import bbdc_words as words


def command(*args):
    out, err = StringIO(), StringIO()
    with patch.object(sys, "argv", ["bbdc_import.py", *map(str, args)]), redirect_stdout(out), redirect_stderr(err):
        code = cli.main()
    return code, json.loads(out.getvalue()), err.getvalue()


class LocalFiles(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="bbdc-test-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def write(self, name, content, encoding="utf-8"):
        path = self.root / name
        path.write_text(content, encoding=encoding)
        return path

    def test_common_formats_and_csv_flag_conflict(self):
        text = self.write("词 表.txt", "1. Resilient\n• look   up to\n- self‑aware\n2、coach’s\nresilient\n", "utf-16")
        desired = ["resilient", "look up to", "self-aware", "coach's"]
        self.assertEqual(words.read_terms(text).words, desired)
        output = self.root / "out.csv"
        code, data, _ = command("prepare", text, "--out", output)
        self.assertEqual((code, data["duplicates"]), (0, 1))
        self.assertEqual(words.read_terms(output).words, desired)
        self.assertEqual(words.read_terms(output).digest, data["digest"])
        flagged = self.write("backup.csv", "lemma,flags\nput,0\nPUT,1\nstay,00\n")
        self.assertEqual(words.read_terms(flagged), words.WordList(["stay"], 1, 1))
        tab = self.write("study.tsv", "说明\t单词\n释义\treliable\n")
        self.assertEqual(words.read_terms(tab).words, ["reliable"])
        csv = self.write("study.csv", "word,meaning\nreliable,\"有用, 可靠\"\n", "utf-8-sig")
        self.assertEqual(words.read_terms(csv).words, ["reliable"])

    def test_invalid_batch_digest_mismatch_and_overwrite_stop_before_network(self):
        broken = self.write("bad.csv", "word,definition\nreach\n数字42,x\nallowed,definition\n")
        valid = self.write("valid.txt", "actual\n")
        existing = self.write("already.csv", "keep")
        with patch.object(cli, "run_remote", side_effect=AssertionError("must not start browser")):
            for args, reason in (
                (("push", broken), "invalid_rows"),
                (("push", valid, "--expect", "stale"), "wordlist_changed"),
                (("push", valid, "--remaining-out", existing), "output_exists"),
            ):
                with self.subTest(reason=reason):
                    code, report, err = command(*args)
                    self.assertEqual((code, report["error"]), (2, reason))
                    self.assertIn(report["hint"], err)
                    if reason == "invalid_rows":
                        self.assertEqual([problem["line"] for problem in report["issues"]], [2, 3])
        self.assertEqual(existing.read_text(), "keep")

    def test_nothing_to_do_is_explicit_before_network(self):
        all_flagged = self.write("all.csv", "lemma,flags\nprior,1\n")
        empty = self.write("empty.txt", " \n")
        remaining = self.root / "empty-recovery.csv"
        with patch.object(cli, "run_remote", side_effect=AssertionError("must not start browser")):
            code, report, _ = command("push", all_flagged, "--remaining-out", remaining)
            self.assertEqual((code, report["status"], report["added"], report["skipped_by_csv_flag"]),
                             (0, "nothing_to_do", 0, 1))
            self.assertEqual(words.read_terms(remaining).words, [])
            code, report, _ = command("push", empty)
            self.assertEqual((code, report["status"], report["added"], report["skipped_by_csv_flag"]),
                             (0, "nothing_to_do", 0, 0))

    def test_exclusive_profile_is_released_after_the_owner_exits(self):
        path = self.root / "profile.lock"
        program = """import sys
from pathlib import Path
import bbdc_remote as r

try:
    with r.exclusive(Path(sys.argv[1]), 'busy', 'test'):
        print('free')
except r.ServiceError as error:
    print(error.code)
"""
        env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
        def other_process():
            return subprocess.check_output([sys.executable, "-B", "-c", program, str(path)],
                                           text=True, env=env, timeout=5).strip()
        with remote.exclusive(path, "busy", "test"):
            self.assertEqual(other_process(), "busy")
        self.assertEqual(other_process(), "free")

    def test_ctrl_c_during_post_is_uncertain_and_keeps_word(self):
        source = self.write("words.txt", "present\nmaybe\nlater\n")
        output = self.root / "remaining.csv"
        def interrupted(args, terms, report, outcomes):
            with patch.object(remote, "list_wordbook", return_value={"present"}), \
                    patch.object(remote, "definition", return_value="meaning"), \
                    patch.object(remote, "add_word", side_effect=KeyboardInterrupt):
                cli.run_batch(terms, None, None, outcomes, lookup_only=False)
        with patch.object(cli, "run_remote", side_effect=interrupted), patch.object(cli, "WORD_GAP", 0):
            code, report, _ = command("push", source, "--remaining-out", output)
        self.assertEqual((code, report["stop_reason"], report["unattempted"]), (1, "interrupted", 1))
        self.assertEqual(report["uncertain"][0]["word"], "maybe")
        self.assertEqual(words.read_terms(output).words, ["maybe", "later"])

    def test_login_wait_distinguishes_timeout_from_window_closed(self):
        from types import SimpleNamespace

        error = remote.ServiceError("login_required", "login", remote_code=20000)
        with patch.object(remote, "check_login", side_effect=error), patch.object(remote, "open_account_page"):
            for pages, expected in [([object()], "login_timeout"), ([], "login_cancelled")]:
                with self.subTest(expected=expected), self.assertRaises(remote.ServiceError) as failure:
                    remote.ensure_login(SimpleNamespace(request=object(), pages=pages), 0, lambda _: None)
                self.assertEqual(failure.exception.code, expected)


class Wire(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, status=200, headers=None):
        body = (json.dumps(data) if isinstance(data, dict) else data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if isinstance(data, dict) else "text/html")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def signed_in(self):
        return self.headers.get("Cookie") == "dummy_local_session=signed_in"

    def auth_needed(self):
        self.send({"result_code": 20000, "error_body": {"user_message": "请登录", "info": "请登录"}})

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/":
            return self.send('<script>setTimeout(() => fetch("/sign-in"), 100)</script>')
        if url.path == "/sign-in":
            return self.send({}, headers={"Set-Cookie": "dummy_local_session=signed_in; Path=/; HttpOnly; Max-Age=600"})
        if url.path == "/favicon.ico":
            return self.send({}, 404)
        query = parse_qs(url.query)
        if url.path == "/loadLexisList.action":
            word = query["word"][0]
            self.server.lookups.append((word, self.headers.get("Cookie"), query["strict"]))
            if word == "nodict":
                return self.send({"wordlist": []})
            if word == "broken":
                return self.send({"unrecognized": []})
            return self.send({"wordlist": [{"interpret": "中文释义"}]})
        if not self.signed_in():
            return self.auth_needed()
        if url.path == "/api/check-login":
            return self.send({"result_code": 200})
        if url.path == "/api/user-new-word":
            page = int(query["page"][0])
            self.server.checks.append(page)
            if self.server.failure == "expired":
                return self.auth_needed()
            if self.server.failure == "limiting":
                return self.send({"result_code": 429}, 429, {"Retry-After": "17"})
            if self.server.failure == "transient" and len(self.server.checks) == 1:
                return self.send({}, 503)
            records = sorted(self.server.present)
            size = 2
            pages = max(1, (len(records) + size - 1) // size)
            result = {"wordList": [{"word": word} for word in records[page * size:(page + 1) * size]],
                      "pageInfo": {"totalRecord": len(records), "pageSize": size, "totalPage": pages,
                                   "currentPage": page + 1}}
            return self.send({"result_code": 200, "data_body": result})
        return self.send({}, 404)

    def do_POST(self):
        if self.path != "/api/user-new-word" or not self.signed_in():
            self.server.post_violations.append(self.path)
            return self.auth_needed()
        data = self.rfile.read(int(self.headers["Content-Length"]))
        prefix = ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode()
        form = BytesParser(policy=policy.default).parsebytes(prefix + data)
        entries = list(form.iter_parts())
        if len(entries) != 1 or entries[0].get_param("name", header="content-disposition") != "newwordlist":
            self.server.post_violations.append("bad_multipart")
            return self.send({}, 400)
        item = json.loads(entries[0].get_payload(decode=True))
        self.server.posts.append(item)
        word = item["word"]
        if word.startswith("reject"):
            return self.send({"result_code": 10001})
        self.server.present.add(word)
        if word == "uncertain":
            return self.send({}, 502)
        return self.send({"result_code": 200})


class WireFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A light local test can still run on hosts without Playwright/Chromium.
        try:
            with remote.open_browser("chromium", use_profile=False):
                pass
        except remote.ServiceError as error:
            if error.code in {"missing_playwright", "missing_browser", "missing_system_libraries"}:
                raise unittest.SkipTest(error.code) from None
            raise
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Wire)
        cls.addClassCleanup(cls.server.server_close)
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.server.shutdown)
        url = f"http://127.0.0.1:{cls.server.server_port}"
        work = tempfile.TemporaryDirectory(prefix="bbdc-browser-test-")
        cls.addClassCleanup(work.cleanup)
        cls.root = Path(work.name)
        fixtures = patch.multiple(remote, BBDC=url, DICTIONARY=url, data_dir=lambda: cls.root / "private browser")
        fixtures.start()
        cls.addClassCleanup(fixtures.stop)
        cls.server.lookups = []
        cls.server.checks = []
        cls.server.posts = []
        cls.server.post_violations = []
        cls.server.present = set()
        with remote.open_browser("chromium") as (_, context):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with unittest.TestCase().assertRaises(remote.ServiceError) as error:
                    remote.check_login(context.request)
                if error.exception.code != "login_required":
                    raise AssertionError(error.exception.code)
                # Run the same handoff, using a local mock login page in headless Chromium.
                remote.ensure_login(context, 10, lambda _: None)

    def setUp(self):
        self.server.lookups.clear()
        self.server.checks.clear()
        self.server.posts.clear()
        self.server.post_violations.clear()
        self.server.present.clear()
        self.server.failure = None
        wait = patch.object(cli, "WORD_GAP", 0)
        wait.start()
        self.addCleanup(wait.stop)

    def source(self, *terms):
        # Each test receives a new output path to exercise the no-overwrite contract.
        folder = Path(tempfile.mkdtemp(dir=self.root))
        path = folder / "selected words.txt"
        path.write_text("\n".join(terms), encoding="utf-8")
        return path, folder / "remaining.csv"

    def test_success_cookie_persistence_check_only_and_wire_contract(self):
        source, recovery = self.source("present", "come across", "nodict")
        self.server.present.add("present")
        _, preview, _ = command("prepare", source)
        code, report, _ = command("push", source, "--expect", preview["digest"], "--remaining-out", recovery)
        self.assertEqual((code, report["added"], report["existing"], report["added_without_definition"]),
                         (0, 2, 1, 1))
        self.assertEqual(words.read_terms(recovery).words, [])
        self.assertEqual(self.server.post_violations, [])
        self.assertEqual(self.server.posts, [
            {"word": word, "info": definition, "course": "*", "wordidx": "*",
             "infoidx": "100", "selection": "*", "opcode": "1"}
            for word, definition in [("come across", "中文释义"), ("nodict", "")]
        ])
        self.assertEqual(self.server.lookups, [("come across", None, ["1"]), ("nodict", None, ["1"])])
        result = self.source("come across", "not there")[0]
        code, report, _ = command("check", result)
        self.assertEqual((code, report["status"], report["existing"], report["missing"]),
                         (0, "checked", 1, 1))
        self.assertEqual(len(self.server.lookups), 2)
        self.assertEqual(len(self.server.posts), 2)

    def test_ambiguous_write_is_not_repeated_and_resume_checks_first(self):
        source, recovery = self.source("first", "uncertain", "later")
        code, half, _ = command("push", source, "--remaining-out", recovery)
        self.assertEqual((code, half["status"], half["added"], half["unattempted"]), (1, "paused", 1, 1))
        self.assertEqual([word["word"] for word in half["uncertain"]], ["uncertain"])
        self.assertEqual([record["word"] for record in self.server.posts], ["first", "uncertain"])
        self.assertEqual(words.read_terms(recovery).words, ["uncertain", "later"])
        code, report, _ = command("push", recovery)
        self.assertEqual((code, report["existing"], report["added"]), (0, 1, 1))
        self.assertEqual([record["word"] for record in self.server.posts], ["first", "uncertain", "later"])

    def test_http_200_login_failure_and_rate_limit_stop_the_queue(self):
        for terms, desired, exit_code in [
            (["expired", "later"], "login_required", 3),
            (["limiting", "later"], "rate_limited", 1),
            (["broken", "later"], "invalid_response", 1),
        ]:
            with self.subTest(report_code=desired):
                self.server.checks.clear()
                self.server.failure = terms[0] if desired != "invalid_response" else None
                source, recovery = self.source(*terms)
                code, report, _ = command("push", source, "--remaining-out", recovery)
                expected_unattempted = 1 if desired == "invalid_response" else 2
                self.assertEqual((code, report["stop_reason"], report["unattempted"]), (exit_code, desired, expected_unattempted))
                self.assertEqual(self.server.posts, [])
                self.assertEqual(words.read_terms(recovery).words, terms)
                if desired == "rate_limited":
                    self.assertEqual(report["retry_after_seconds"], 17)
                    self.assertEqual(len(self.server.checks), 1)

    def test_read_retry_and_repeated_business_rejection(self):
        source, _ = self.source("transient", "reject a", "reject b", "reject c", "after")
        self.server.failure = "transient"
        code, report, _ = command("push", source)
        self.assertEqual((code, report["added"], report["unattempted"], report["stop_reason"]),
                         (1, 1, 1, "repeated_rejection"))
        self.assertEqual([word["word"] for word in report["failed"]], ["reject a", "reject b", "reject c"])
        self.assertEqual(len(self.server.checks), 2)
        self.assertEqual([record["word"] for record in self.server.posts].count("transient"), 1)


if __name__ == "__main__":
    unittest.main()
