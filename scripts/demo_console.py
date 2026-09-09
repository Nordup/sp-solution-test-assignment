"""Optional local browser console for the unchanged headed agent runner."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from rich.console import Console

from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.runner import run_agent, safe_name

PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent console</title><style nonce="NONCE">
body{background:#10151e;color:#e7edf5;font:16px system-ui;margin:24px;max-width:1200px}
h1{font-size:25px;margin-bottom:8px}p{color:#bdc8d8}textarea{width:96%;padding:12px;background:#1b2431;color:inherit;border:1px solid #52637b;border-radius:6px;font:inherit}button{padding:10px 18px;margin:8px 8px 8px 0;border:0;border-radius:5px;background:#95c6ff;color:#112039;font-weight:600;cursor:pointer}button:disabled{opacity:.4;cursor:default}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace;background:#171f2b;padding:16px;border:1px solid #34445a;border-radius:6px}#output{height:52vh;overflow:auto}#question{border:2px solid #e1b565;padding:16px}#error{color:#ffb1b1}.hidden{display:none}</style>
<h1>Agent console</h1><p id="mode">Authenticating local session…</p>
<p>Live tool calls, results, and approval requests.</p>
<label for="task">Task — describe the result you want</label><textarea id="task" rows="3"></textarea>
<button id="start">Start task</button><span id="status"></span><p id="error"></p>
<section id="question" class="hidden"><h2>Waiting for you</h2><pre id="details"></pre>
<div id="approval"><button id="approve">Approve this exact action</button><button id="deny">Deny</button></div>
<div id="clarification"><textarea id="answer" rows="2" placeholder="Reply to the agent"></textarea><button id="reply">Send reply</button></div>
<button id="pause">Save and pause</button></section>
<pre id="output" aria-label="Actual live agent output"></pre><pre id="result" class="hidden"></pre>
<script nonce="NONCE">
const $=id=>document.getElementById(id);const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
let csrf='',pending=null,busy=false;
async function request(path,body){const options={headers:{Authorization:'Bearer '+token}};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-CSRF-Token']=csrf;options.body=JSON.stringify(body)}const response=await fetch(path,options);const value=await response.json();if(!response.ok)throw Error(value.error||'Request rejected');return value}
async function refresh(){try{const state=await request('/state');csrf=state.csrf;pending=state.pending;$('mode').textContent=state.label+' | Model '+state.model+' | $'+state.budget+' maximum per task | Profile '+state.profile;$('status').textContent=state.active?'Running / waiting':'Idle';$('start').disabled=state.active||busy;$('task').readOnly=state.active;if(state.active)$('task').value=state.task;
const output=$('output');const bottom=output.scrollHeight-output.scrollTop-output.clientHeight<80;output.textContent=state.output;if(bottom)output.scrollTop=output.scrollHeight;
$('question').classList.toggle('hidden',!pending);if(pending){$('details').textContent=JSON.stringify(pending.question,null,2);const approval=pending.question.kind==='approval';$('approval').classList.toggle('hidden',!approval);$('clarification').classList.toggle('hidden',approval)}$('result').classList.toggle('hidden',!state.result);if(state.result)$('result').textContent=JSON.stringify(state.result,null,2);
}catch(e){$('error').textContent=e.message}}
async function send(path,body){busy=true;try{await request(path,body);$('error').textContent='';await refresh()}catch(e){$('error').textContent=e.message}finally{busy=false}}
$('start').onclick=()=>send('/start',{task:$('task').value});
function answer(value){if(!pending||busy)return;send('/answer',{question_id:pending.id,...value})}
$('approve').onclick=()=>answer({request_id:pending.question.request_id,approved:true});$('deny').onclick=()=>answer({request_id:pending.question.request_id,approved:false});$('reply').onclick=()=>answer({answer:$('answer').value});$('pause').onclick=()=>answer({pause:true});
refresh();setInterval(refresh,700);
</script></html>"""


class AdmissionError(ValueError):
    pass


class Output:
    def __init__(self, app):
        self.app = app

    def write(self, text):
        with self.app.lock:
            self.app.output += text
            if len(self.app.output) > 1_000_000:
                self.app.output = (
                    "[Earlier console output omitted; see private run events.]\n"
                    + self.app.output[-900_000:]
                )
        return len(text)

    def flush(self):
        pass


class DemoApp:
    def __init__(
        self,
        settings,
        *,
        url,
        profile,
        release_session,
        synthetic=False,
        runner=run_agent,
        browser_factory=BrowserSession,
    ):
        self.settings, self.url, self.profile = settings, url, safe_name(profile)
        self.release_session = safe_name(release_session)
        self.synthetic, self.runner = synthetic, runner
        self.browser_factory = browser_factory
        self.token, self.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.output, self.result, self.pending = "", None, None
        self.active, self.closed = False, False
        self.answer_future = None
        self.job = None
        self.run_task = None
        self.task = ""
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.console = Console(
            file=Output(self), width=100, color_system=None, force_terminal=False
        )

    def state(self):
        with self.lock:
            return {
                "active": self.active,
                "pending": self.pending,
                "output": self.output,
                "result": self.result,
                "csrf": self.csrf,
                "profile": self.profile,
                "budget": self.settings.budget_usd,
                "model": self.settings.model,
                "task": self.task,
                "label": "Synthetic evaluation — local fixture"
                if self.synthetic
                else "Live account",
            }

    def start(self, body):
        if (
            set(body) != {"task"}
            or not isinstance(body["task"], str)
            or not body["task"].strip()
            or len(body["task"].encode()) > 12000
        ):
            raise AdmissionError(
                "Provide a nonempty task of at most 12000 UTF-8 bytes."
            )
        with self.lock:
            if self.active or self.closed:
                raise AdmissionError(
                    "A run is already active or the console is closing."
                )
            self.active, self.output, self.result = True, "", None
            self.task = body["task"]
            self.job = asyncio.run_coroutine_threadsafe(
                self._run(body["task"]), self.loop
            )

    async def _run(self, task):
        self.run_task = asyncio.current_task()
        try:
            result = await self.runner(
                self.settings,
                task=task,
                url=self.url,
                profile=self.profile,
                headless=False,
                responder=self.respond,
                console=self.console,
                release_session=self.release_session,
                synthetic=self.synthetic,
                browser_factory=self.browser_factory,
            )
            self.console.print_json(data=result)
            with self.lock:
                self.result = result
        except asyncio.CancelledError:
            with self.lock:
                self.result = {
                    "status": "cancelled",
                    "note": "Inspect the saved run journal before resuming.",
                }
        except Exception as exc:  # noqa: BLE001 — isolate UI and redact exception values
            # Never return exception values that may contain credentials or request headers.
            with self.lock:
                self.result = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "note": "Run stopped. Inspect private run artifacts; no success is claimed.",
                }
        finally:
            with self.lock:
                self.active, self.pending, self.answer_future = False, None, None

    async def respond(self, question):
        future = self.loop.create_future()
        with self.lock:
            self.answer_future = future
            self.pending = {"id": secrets.token_urlsafe(24), "question": question}
        return await future

    def answer(self, body):
        with self.lock:
            if not self.pending or body.get("question_id") != self.pending["id"]:
                raise AdmissionError(
                    "This question is absent, stale or already answered."
                )
            question = self.pending["question"]
            if set(body) == {"question_id", "pause"} and body["pause"] is True:
                value = None
            elif question["kind"] == "approval":
                if (
                    set(body) != {"question_id", "request_id", "approved"}
                    or body["request_id"] != question["request_id"]
                    or type(body["approved"]) is not bool
                ):
                    raise AdmissionError(
                        "Approval must match this exact request and explicit boolean decision."
                    )
                value = {
                    "request_id": question["request_id"],
                    "approved": body["approved"],
                }
            else:
                if (
                    set(body) != {"question_id", "answer"}
                    or not isinstance(body["answer"], str)
                    or not body["answer"].strip()
                    or len(body["answer"].encode()) > 12000
                ):
                    raise AdmissionError(
                        "Provide a nonempty reply of at most 12000 UTF-8 bytes."
                    )
                value = {"answer": body["answer"]}
            future = self.answer_future
            if future is None or future.done():
                raise AdmissionError("This question is no longer waiting.")
            self.pending, self.answer_future = None, None
            self.loop.call_soon_threadsafe(self._deliver, future, value)

    @staticmethod
    def _deliver(future, value):
        if not future.done():
            future.set_result(value)

    def close(self):
        with self.lock:
            self.closed = True

        async def stop():
            # Cancel only our runner so Playwright driver tasks can finish cleanup.
            task = self.run_task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(timeout=30)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


def create_server(app, port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass  # Do not log URLs, tokens, task contents or authorization headers.

        def reply(self, status, body, content_type="application/json", nonce=None):
            raw = (
                body.encode()
                if isinstance(body, str)
                else json.dumps(body, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; connect-src 'self'; script-src 'nonce-"
                + (nonce or "")
                + "'; style-src 'nonce-"
                + (nonce or "")
                + "'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self, mutation=False):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.reply(403, {"error": "Invalid Host."})
                return False
            if not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(),
                ("Bearer " + app.token).encode(),
            ):
                self.reply(
                    401,
                    {
                        "error": "Open the original console link containing its session token."
                    },
                )
                return False
            if mutation and (
                self.headers.get("Origin")
                != f"http://127.0.0.1:{self.server.server_port}"
                or not hmac.compare_digest(
                    self.headers.get("X-CSRF-Token", "").encode(), app.csrf.encode()
                )
            ):
                self.reply(403, {"error": "Origin or CSRF token rejected."})
                return False
            return True

        def do_GET(self):
            if self.path == "/":
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    return self.reply(403, {"error": "Invalid Host."})
                nonce = secrets.token_urlsafe(24)
                return self.reply(200, PAGE.replace("NONCE", nonce), "text/html", nonce)
            if not self.authorized():
                return
            if self.path == "/state":
                return self.reply(200, app.state())
            self.reply(404, {"error": "Not found."})

        def do_POST(self):
            if not self.authorized(mutation=True):
                return
            if self.headers.get("Content-Type") != "application/json":
                return self.reply(415, {"error": "JSON required."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20000:
                    raise ValueError("Invalid body size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise TypeError("JSON object required.")
                if self.path == "/start":
                    app.start(body)
                elif self.path == "/answer":
                    app.answer(body)
                else:
                    return self.reply(404, {"error": "Not found."})
            except AdmissionError as exc:
                return self.reply(409, {"error": str(exc)})
            except (ValueError, TypeError, UnicodeError):
                return self.reply(400, {"error": "Invalid JSON request or body size."})
            self.reply(200, {"accepted": True})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument(
        "--fixture", choices=["food_previous_order", "mail_latest_10", "jobs_resume_3"]
    )
    parser.add_argument("--seed", type=int, default=102)
    parser.add_argument(
        "--profile", help="Defaults to demo for live URLs, demo-synthetic for fixtures."
    )
    parser.add_argument("--release-session", required=True)
    parser.add_argument("--budget-usd", type=float, default=5)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.url and urlsplit(args.url).scheme not in {"http", "https"}:
        parser.error("The starting URL must use HTTP or HTTPS.")
    settings = Settings.load(budget_usd=args.budget_usd)
    with contextlib.ExitStack() as stack:
        url = args.url
        browser_factory = BrowserSession
        if args.fixture:
            from evals.fixtures import FixtureServer
            from evals.run import fixture_browser_factory

            fixture = stack.enter_context(FixtureServer(args.fixture, args.seed))
            url = fixture.url
            browser_factory = fixture_browser_factory(fixture)
        app = DemoApp(
            settings,
            url=url,
            profile=args.profile or ("demo-synthetic" if args.fixture else "demo"),
            release_session=args.release_session,
            synthetic=bool(args.fixture),
            browser_factory=browser_factory,
        )
        stack.callback(app.close)
        server = create_server(app, args.port)
        stack.callback(server.server_close)
        print(
            f"Open this private local console link: http://127.0.0.1:{server.server_port}/#{app.token}",
            flush=True,
        )
        print(
            "Synthetic fixture" if args.fixture else "Live-site account session",
            flush=True,
        )
        print(
            "Close the current profile holder before starting. Ctrl-C stops this server safely.",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
