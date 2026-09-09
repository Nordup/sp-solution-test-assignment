"""Generic, reference-bound Playwright adapter. All effect authorization is upstream.

No site routes or selectors, JavaScript, cookies, or local file tools are exposed
in the actor API. Trusted DOM inspection below supplies the policy with evidence.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright


class BrowserError(Exception):
    def __init__(self, code: str, message: str, uncertain: bool = False):
        super().__init__(message)
        self.code, self.message, self.uncertain = code, message, uncertain

    def as_dict(self):
        return {"code": self.code, "message": self.message, "uncertain": self.uncertain}


# Reads only rendered text and visible controls; never hidden stores or password values.
_CONTEXT_JS = """el => {
 const visible = x => !!(x.getClientRects().length) && getComputedStyle(x).visibility !== 'hidden';
 const field = x => ({tag:x.tagName.toLowerCase(), type:x.type || '',
   name:x.getAttribute('aria-label') || Array.from(x.labels || []).map(l=>l.innerText).join(' ') || x.name || '',
   value:x.type === 'password' ? '[REDACTED]' : x.value,
   checked:!!x.checked, disabled:!!x.disabled,
   options:x.tagName === 'SELECT' ? Array.from(x.options).map(o=>({label:o.label,value:o.value,selected:o.selected})) : undefined});
 const fields = root => Array.from(root.querySelectorAll('input,textarea,select')).filter(visible).map(field);
 const form = el.closest('form');
 const region = form || el.closest('dialog,article,section,[role=dialog],main') || el.parentElement || el;
 const doc = el.ownerDocument;
 return {tag:el.tagName.toLowerCase(), role:el.getAttribute('role') || '',
   type:el.type || '', name:el.getAttribute('aria-label') || Array.from(el.labels || []).map(l=>l.innerText).join(' ') || el.innerText || el.getAttribute('title') || '',
   text:el.innerText || '', href:el.href || '', disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',
   editable:el.isContentEditable, target:el.target || '',
   value:el.type === 'password' ? '[REDACTED]' : el.value,
   checked:!!el.checked, form_action:form ? form.action : '', form_method:form ? form.method : '',
   context:region.innerText || '', fields:fields(form || doc),
   page_text:doc.body ? doc.body.innerText : '', document_url:doc.URL,
   attached:el.isConnected, visible:visible(el), in_frame:doc.defaultView !== doc.defaultView.top};
}"""
_PAGE_JS = """() => ({text:document.body ? document.body.innerText : '',
 fields:Array.from(document.querySelectorAll('input,textarea,select')).filter(x=>x.getClientRects().length && getComputedStyle(x).visibility !== 'hidden').map(x=>({name:x.getAttribute('aria-label') || x.name || '',type:x.type,value:x.type==='password'?'[REDACTED]':x.value,checked:!!x.checked}))})"""
_REF = re.compile(r"\[ref=([A-Za-z0-9]+)\]")
_KEYS = {
    "Enter",
    "Tab",
    "Escape",
    "ArrowDown",
    "ArrowUp",
    "ArrowLeft",
    "ArrowRight",
    "Space",
    "Home",
    "End",
    "PageDown",
    "PageUp",
    "Backspace",
    "Delete",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _bounded(value: Any, limit: int = 1600) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, list):
        return [_bounded(v, limit) for v in value[:60]]
    if isinstance(value, dict):
        return {k: _bounded(v, limit) for k, v in value.items()}
    return value


class BrowserSession:
    MAX_OBSERVATION_CHARS = 18000
    ACTION_TIMEOUT_MS = 5000

    def __init__(
        self, profile: Path, headless: bool = False, artifact_dir: Path | None = None
    ):
        self.profile = Path(profile).resolve()
        self.headless = headless
        self.artifact_dir = Path(
            artifact_dir or self.profile.parent.parent / "evidence"
        ).resolve()
        self.lock = asyncio.Lock()
        self.context = None
        self.page = None
        self.generation = uuid4().hex
        self.revision = 0
        self._pw = None
        self._profile_lock = None
        self._pages: dict[str, Any] = {}
        self._registry: dict[str, dict] = {}
        self._observation: dict | None = None
        self._snapshot_text = ""
        self._dialogs: list[dict] = []
        self._http_status: dict[str, dict] = {}
        self._closed = True

    async def start(self, url: str | None = None):
        async with self.lock:
            if self.context is not None and not self._closed:
                raise BrowserError(
                    "already_started", "Browser session is already running"
                )
            if url:
                self._validate_url(url)
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.profile.chmod(0o700)
            self._profile_lock = (self.profile / ".agent-profile.lock").open("a+")
            try:
                fcntl.flock(self._profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._profile_lock.close()
                self._profile_lock = None
                raise BrowserError(
                    "profile_busy",
                    "Profile is already in use; close its browser normally",
                ) from exc
            try:
                self._pw = await async_playwright().start()
                self.context = await self._pw.chromium.launch_persistent_context(
                    str(self.profile), headless=self.headless, accept_downloads=False
                )
                self.context.set_default_timeout(self.ACTION_TIMEOUT_MS)
                self.context.set_default_navigation_timeout(15000)
                self._closed = False
                self.generation = uuid4().hex
                self._pages, self._registry = {}, {}
                self._observation = None
                self.context.on("close", lambda _: setattr(self, "_closed", True))
                self.context.on("page", self._register_page)
                for page in self.context.pages:
                    self._register_page(page)
                self.page = (
                    self.context.pages[0]
                    if self.context.pages
                    else await self.context.new_page()
                )
                if url:
                    await self.page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                await self._close_unlocked()
                if isinstance(exc, BrowserError):
                    raise
                # Chromium's singleton lock also protects profiles opened by other launchers.
                code = (
                    "profile_busy"
                    if "Singleton" in str(exc) or "ProcessSingleton" in str(exc)
                    else "browser_start_failed"
                )
                raise BrowserError(
                    code,
                    "Could not open browser; check installation, display, and profile ownership",
                ) from exc

    def _register_page(self, page):
        if page in self._pages.values():
            return
        self._pages["p" + uuid4().hex[:10]] = page
        page.on("dialog", self._dismiss_dialog)
        page.on("response", self._record_response)

    def _record_response(self, response):
        if response.request.is_navigation_request():
            self._http_status[response.url] = {
                "status": response.status,
                "retry_after": response.headers.get("retry-after"),
            }

    async def _dismiss_dialog(self, dialog):
        self._dialogs.append(
            {
                "type": dialog.type,
                "message": dialog.message[:500],
                "disposition": "dismissed",
            }
        )
        await dialog.dismiss()

    async def close(self):
        async with self.lock:
            await self._close_unlocked()

    async def _close_unlocked(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = None
            self.page = None
            self._closed = True
            self._registry.clear()
            self._observation = None
            if self._pw:
                await self._pw.stop()
                self._pw = None
            if self._profile_lock:
                fcntl.flock(self._profile_lock, fcntl.LOCK_UN)
                self._profile_lock.close()
                self._profile_lock = None

    @property
    def page_id(self):
        return next((k for k, p in self._pages.items() if p is self.page), "")

    def _ensure_open(self):
        if self._closed or self.page is None or self.page.is_closed():
            raise BrowserError(
                "browser_disconnected",
                "Browser or active page closed; reopen and observe before continuing",
            )

    @staticmethod
    def _validate_url(url):
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme in {"https", "http"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise BrowserError(
                "invalid_url",
                "Only HTTP(S) URLs without embedded credentials are supported",
            )

    def _check_observation(self, observation_id):
        self._ensure_open()
        if (
            not self._observation
            or self._observation["id"] != observation_id
            or self._observation["page_id"] != self.page_id
            or self._observation["generation"] != self.generation
        ):
            raise BrowserError(
                "stale_observation", "Observation is no longer current; observe again"
            )
        if self._observation["url"] != self.page.url:
            raise BrowserError("stale_observation", "Page navigated since observation")

    async def _metadata(self, ref):
        locator = self.page.locator("aria-ref=" + ref)
        try:
            if await locator.count() != 1:
                raise BrowserError("stale_ref", "Target no longer uniquely resolves")
            raw = await locator.evaluate(_CONTEXT_JS)
        except PlaywrightError as exc:
            raise BrowserError(
                "stale_ref", "Observed target detached or changed; observe again"
            ) from exc
        if not raw["attached"] or not raw["visible"]:
            raise BrowserError("stale_ref", "Target is no longer visible")
        if raw.get("in_frame"):
            raw["outer_document"] = await self.page.evaluate(_PAGE_JS)
        fingerprint = _digest(
            {
                "generation": self.generation,
                "page_id": self.page_id,
                "url": self.page.url,
                "ref": ref,
                "target": raw,
            }
        )
        context = _bounded(raw)
        # Page prose is auxiliary evidence. Its truncation must not make an
        # otherwise complete navigation target impossible to use. Exact form
        # payloads remain complete or explicitly prevent dispatch.
        fields = raw.get("fields", [])
        payload_complete = (
            len(fields) <= 60
            and len(json.dumps(fields, ensure_ascii=False).encode()) <= 12000
        )
        context["fields"] = fields if payload_complete else _bounded(fields)
        context["context"] = _bounded(raw.get("context", ""), 8000)
        context["context_truncated"] = len(raw.get("context", "")) > 8000
        context["page_text_truncated"] = len(raw.get("page_text", "")) > 1600
        context["fingerprint"] = fingerprint
        context["ref"] = ref
        context["page_id"] = self.page_id
        context["generation"] = self.generation
        context["url"] = self.page.url
        context["context_complete"] = (
            payload_complete
            and all(
                len(str(raw.get(key, ""))) <= 1600
                for key in ("name", "text", "href", "value")
            )
            and (not raw.get("form_action") or len(raw.get("context", "")) <= 8000)
        )
        return context

    async def observe(self, offset: int = 0, scope: str | None = None) -> dict:
        async with self.lock:
            self._ensure_open()
            if offset < 0:
                raise BrowserError(
                    "invalid_offset", "Observation offset must be nonnegative"
                )
            try:
                if scope:
                    if scope not in self._registry:
                        raise BrowserError(
                            "unknown_ref",
                            "Read scope must be a ref delivered in the current observation",
                        )
                    await self._describe_unlocked(scope, self._observation["id"])
                    snapshot = await self.page.locator(
                        "aria-ref=" + scope
                    ).aria_snapshot(mode="ai", depth=30)
                else:
                    snapshot = await self.page.aria_snapshot(mode="ai", depth=30)
                # Playwright AI snapshots DO include password values. Redact the
                # entire password-node line before pagination, registry, or output.
                # Generic type selectors are trusted adapter internals, never actor tools.
                # Taking a second scoped AI snapshot would replace Playwright's
                # reference registry. Inspect the refs in THIS snapshot instead.
                password_refs = set()
                for candidate in dict.fromkeys(_REF.findall(snapshot)):
                    try:
                        is_password = await self.page.locator(
                            "aria-ref=" + candidate
                        ).evaluate("el => el.type === 'password'")
                        if is_password:
                            password_refs.add(candidate)
                    except PlaywrightError:
                        continue
                if password_refs:
                    snapshot = "\n".join(
                        "  - textbox [password redacted]"
                        if any(f"[ref={ref}]" in line for ref in password_refs)
                        else line
                        for line in snapshot.splitlines()
                    )
                if offset > len(snapshot):
                    raise BrowserError(
                        "invalid_offset",
                        "Page content changed or continuation offset is out of range; restart reading",
                    )
                if offset and snapshot != self._snapshot_text:
                    raise BrowserError(
                        "stale_continuation",
                        "Page changed during pagination; restart reading at offset zero",
                    )
                self._snapshot_text = snapshot
                text = (
                    snapshot[offset:]
                    .encode("utf-8")[: self.MAX_OBSERVATION_CHARS]
                    .decode("utf-8", errors="ignore")
                )
                end = offset + len(text)
                refs = list(dict.fromkeys(_REF.findall(text)))
                registry = {}
                for ref in refs:
                    try:
                        meta = await self._metadata(ref)
                        if meta.get("type") != "password":
                            registry[ref] = meta
                    except BrowserError:
                        # Detached nodes are deliberately not actionable.
                        continue
                self.revision += 1
                self._registry = registry
                self._observation = {
                    "id": "obs-" + uuid4().hex,
                    "generation": self.generation,
                    "page_id": self.page_id,
                    "revision": self.revision,
                    "url": self.page.url,
                    "title": (await self.page.title())[:300],
                    "text": text,
                    "refs": list(registry),
                    "truncated": end < len(snapshot),
                    "next_offset": end if end < len(snapshot) else None,
                    "offset": offset,
                    "tabs": await self._tabs(),
                    "http": self._http_status.get(self.page.url, {}),
                }
                return dict(self._observation)
            except PlaywrightError as exc:
                raise BrowserError(
                    "observation_failed",
                    "Page unavailable while observing; check browser state",
                ) from exc

    async def describe(self, ref: str, observation_id: str) -> dict:
        async with self.lock:
            return await self._describe_unlocked(ref, observation_id)

    async def _describe_unlocked(self, ref, observation_id):
        self._check_observation(observation_id)
        if ref not in self._registry:
            raise BrowserError(
                "unknown_ref", "Ref was not delivered as actionable in this observation"
            )
        current = await self._metadata(ref)
        if current["fingerprint"] != self._registry[ref]["fingerprint"]:
            raise BrowserError(
                "stale_ref",
                "Target or effect context changed since observation; observe again",
            )
        if current.get("disabled"):
            raise BrowserError("disabled", "Observed control is disabled")
        return current

    async def action_context(self, tool: str, args: dict, observation_id: str) -> dict:
        async with self.lock:
            return await self._action_context_unlocked(tool, args, observation_id)

    async def _action_context_unlocked(self, tool, args, observation_id):
        self._check_observation(observation_id)
        if args.get("ref"):
            context = await self._describe_unlocked(args["ref"], observation_id)
        else:
            raw = await self.page.evaluate(_PAGE_JS)
            context = _bounded(raw)
            context.update(
                {
                    "page_id": self.page_id,
                    "generation": self.generation,
                    "url": self.page.url,
                    "fingerprint": _digest(
                        {
                            "generation": self.generation,
                            "page": self.page_id,
                            "url": self.page.url,
                            "context": raw,
                        }
                    ),
                }
            )
        if tool == "navigate":
            self._validate_url(args.get("url"))
        context = dict(context)
        context["action"] = {"tool": tool, "args": args}
        context["fingerprint"] = _digest(
            {"context": context["fingerprint"], "tool": tool, "args": args}
        )
        return context

    async def _tabs(self):
        return [
            {"page_id": k, "url": p.url, "active": p is self.page}
            for k, p in self._pages.items()
            if not p.is_closed()
        ]

    async def execute(
        self,
        tool: str,
        args: dict,
        observation_id: str,
        expected_fingerprint: str | None = None,
        before_dispatch=None,
    ) -> dict:
        """Execute once. Caller must authorize/journal first; this never retries effects."""
        if tool in {"observe", "read"}:
            return await self.observe(
                offset=args.get("offset", 0), scope=args.get("ref")
            )
        if tool == "screenshot":
            return await self.screenshot()
        async with self.lock:
            context = await self._action_context_unlocked(tool, args, observation_id)
            if expected_fingerprint and context["fingerprint"] != expected_fingerprint:
                raise BrowserError(
                    "approval_changed",
                    "Action context changed after review; new approval required",
                )
            dialog_start = len(self._dialogs)
            locator = (
                self.page.locator("aria-ref=" + args["ref"])
                if args.get("ref")
                else None
            )
            if tool in {"click", "fill", "select", "press"} and locator is None:
                raise BrowserError(
                    "missing_ref", "This action requires a current observed ref"
                )
            if tool in {"fill", "select"} and (
                not isinstance(args.get("value"), str) or len(args["value"]) > 10000
            ):
                raise BrowserError(
                    "invalid_value", "Value must be text of at most 10000 characters"
                )
            if tool == "press" and args.get("key") not in _KEYS:
                raise BrowserError(
                    "invalid_key",
                    "Unsupported key; system/browser shortcuts are unavailable",
                )
            if tool == "scroll" and args.get("direction", "down") not in {
                "up",
                "down",
                "left",
                "right",
            }:
                raise BrowserError(
                    "invalid_direction", "Scroll direction must be up/down/left/right"
                )
            if tool not in {
                "navigate",
                "back",
                "click",
                "fill",
                "select",
                "press",
                "scroll",
                "tabs",
                "switch_tab",
                "close_tab",
            }:
                raise BrowserError("unknown_tool", "Unsupported browser tool")
            dispatched = False

            async def admit():
                # Revalidate immediately before durable admission, including after
                # Playwright's actionability wait. No external effect precedes it.
                fresh = await self._action_context_unlocked(tool, args, observation_id)
                if fresh["fingerprint"] != context["fingerprint"]:
                    raise BrowserError(
                        "approval_changed", "Action changed before dispatch"
                    )
                if before_dispatch:
                    result = before_dispatch()
                    if inspect.isawaitable(result):
                        await result

            try:
                if tool == "tabs":
                    return {"tabs": await self._tabs()}
                if tool in {"switch_tab", "close_tab"}:
                    target = self._pages.get(args.get("page_id"))
                    if target is None or target.is_closed():
                        raise BrowserError(
                            "unknown_page", "Tab must be a currently known page"
                        )
                    if tool == "switch_tab":
                        await admit()
                        await target.bring_to_front()
                        self.page = target
                    else:
                        await admit()
                        dispatched = True
                        await target.close()
                        if target is self.page:
                            self.page = next(
                                (p for p in self._pages.values() if not p.is_closed()),
                                None,
                            )
                elif tool == "navigate":
                    await admit()
                    dispatched = True
                    await self.page.goto(args["url"], wait_until="domcontentloaded")
                elif tool == "back":
                    await admit()
                    dispatched = True
                    await self.page.go_back(wait_until="domcontentloaded")
                elif tool == "click":
                    # Trial checks overlays/actionability without dispatch; never force.
                    await locator.click(trial=True)
                    await admit()
                    dispatched = True
                    await locator.click()
                elif tool == "fill":
                    await admit()
                    dispatched = True
                    await locator.fill(args["value"])
                    if await locator.input_value() != args["value"]:
                        raise BrowserError(
                            "readback_mismatch",
                            "Field value did not match entered value",
                            True,
                        )
                elif tool == "select":
                    options = await locator.evaluate(
                        "el => Array.from(el.options || []).map(o => ({value:o.value,label:o.label,disabled:o.disabled}))"
                    )
                    matches = [
                        option
                        for option in options
                        if args["value"] in {option["value"], option["label"]}
                    ]
                    if len(matches) != 1 or matches[0]["disabled"]:
                        raise BrowserError(
                            "ambiguous_option",
                            "Select requires one enabled observed option value or exact label.",
                        )
                    selected_value = matches[0]["value"]
                    await admit()
                    dispatched = True
                    await locator.select_option(value=selected_value)
                    if await locator.input_value() != selected_value:
                        raise BrowserError(
                            "readback_mismatch",
                            "Selection did not match requested value",
                            True,
                        )
                elif tool == "press":
                    await admit()
                    dispatched = True
                    await locator.press(args["key"])
                elif tool == "scroll":
                    direction = args.get("direction", "down")
                    delta = {
                        "down": (0, 600),
                        "up": (0, -600),
                        "left": (-600, 0),
                        "right": (600, 0),
                    }[direction]
                    await admit()
                    dispatched = True
                    if locator:
                        await locator.hover()
                    await self.page.mouse.wheel(*delta)
                if self.page and not self.page.is_closed():
                    # DOM readiness, not network-idle polling on streaming pages.
                    await self.page.wait_for_load_state(
                        "domcontentloaded", timeout=5000
                    )
                if len(self._dialogs) > dialog_start:
                    raise BrowserError(
                        "dialog_interrupted",
                        "Native dialog was dismissed; earlier effects may have occurred. Inspect before any retry.",
                        True,
                    )
                self._registry.clear()
                self._observation = None
                return {
                    "status": "executed",
                    "tool": tool,
                    "page_id": self.page_id,
                    "url": self.page.url if self.page else "",
                    "tabs": await self._tabs(),
                    "dialogs": self._dialogs[dialog_start:],
                    "requires_observation": True,
                }
            except PlaywrightError as exc:
                self._registry.clear()
                self._observation = None
                code = (
                    "browser_disconnected"
                    if self._closed or not self.page or self.page.is_closed()
                    else "action_failed"
                )
                raise BrowserError(
                    code,
                    "Action interrupted. Observe current state before deciding whether another action is appropriate.",
                    dispatched,
                ) from exc
            finally:
                if dispatched:
                    self._registry.clear()
                    self._observation = None

    async def screenshot(self) -> dict:
        async with self.lock:
            self._ensure_open()
            # Suppress even visual password contents in screenshots; no cookie access.
            self.artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.artifact_dir.chmod(0o700)
            path = self.artifact_dir / ("screenshot-" + uuid4().hex + ".png")
            masks = [
                frame.locator('input[type="password"]') for frame in self.page.frames
            ]
            await self.page.screenshot(path=str(path), full_page=False, mask=masks)
            path.chmod(0o600)
            return {
                "path": str(path),
                "page_id": self.page_id,
                "generation": self.generation,
                "url": self.page.url,
            }
