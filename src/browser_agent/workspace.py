"""Local browser discovery and lifecycle, with page tools on the active session."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import uuid4

from .browser import BrowserError, BrowserSession

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_BROWSER_WORDS = ("brave", "chrome", "chromium", "edge", "msedge")
_PORT = re.compile(r"(?:\[[^]]+\]|[^:]+):(\d+)(?:\s+\(LISTEN\))?$")
_MAX_DISCOVERIES = 8
_MAX_TABS = 4
_MAX_METADATA_CHARS = 12000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_OPENER = build_opener(_NoRedirect, ProxyHandler({}))


def _safe_url(value, limit=1000):
    try:
        parsed = urlsplit(str(value))
        if not parsed.scheme or not parsed.netloc:
            return str(value)[:limit]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:limit]
    except ValueError:
        return str(value)[:limit]


def _fetch_json(url, limit):
    response = None
    try:
        response = _OPENER.open(
            Request(url, headers={"Accept": "application/json"}), timeout=0.6
        )
        body = response.read(limit + 1)
        if len(body) > limit:
            return None
        return json.loads(body.decode("utf-8"))
    except (HTTPError, OSError, UnicodeDecodeError, ValueError, URLError):
        return None
    finally:
        if response is not None:
            response.close()


def _websocket_is_local(value, port, prefix):
    try:
        parsed = urlsplit(str(value))
        return (
            parsed.scheme in {"ws", "wss"}
            and parsed.hostname in _LOCAL_HOSTS
            and parsed.port == port
            and parsed.path.startswith(prefix)
            and not parsed.username
            and not parsed.password
        )
    except (TypeError, ValueError):
        return False


def _ports_from_lsof():
    """Return likely Chromium debug ports without exposing process command lines."""
    try:
        result = subprocess.run(
            [
                "lsof",
                "-nP",
                "-Fpcn",
                "-a",
                "-u",
                str(os.getuid()),
                "-iTCP",
                "-sTCP:LISTEN",
            ],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ports = {}
    command = ""
    for line in result.stdout.splitlines()[:240]:
        if not line:
            continue
        field, value = line[0], line[1:]
        if field == "c":
            command = value[:80]
            continue
        if field != "n" or not command:
            continue
        normalized = re.sub(r"[^a-z0-9]", "", command.casefold())
        if not any(word in normalized for word in _BROWSER_WORDS):
            continue
        match = _PORT.search(value)
        if not match:
            continue
        port = int(match.group(1))
        if 1 <= port <= 65535:
            ports.setdefault(port, command)
    return [(port, command) for port, command in list(ports.items())[:32]]


def _probe(port, owner):
    base = f"http://127.0.0.1:{port}"
    version = _fetch_json(base + "/json/version", 32768)
    if not isinstance(version, dict) or not _websocket_is_local(
        version.get("webSocketDebuggerUrl"), port, "/devtools/browser/"
    ):
        return None
    tabs_payload = _fetch_json(base + "/json/list", 131072)
    tabs = []
    if isinstance(tabs_payload, list):
        for item in tabs_payload[: _MAX_TABS * 2]:
            if not isinstance(item, dict) or not _websocket_is_local(
                item.get("webSocketDebuggerUrl"), port, "/devtools/"
            ):
                continue
            tabs.append(
                {
                    "title": str(item.get("title", ""))[:120],
                    "url": _safe_url(item.get("url", ""), 400),
                    "type": str(item.get("type", ""))[:40],
                }
            )
    websocket = version["webSocketDebuggerUrl"]
    discovery_id = "d-" + hashlib.sha256(websocket.encode()).hexdigest()[:14]
    return {
        "discovery_id": discovery_id,
        "browser": str(version.get("Browser", "Chromium"))[:120],
        "owner": owner[:80],
        "port": port,
        "tabs": tabs,
        "_endpoint": base,
    }


def _discover_sync():
    candidates = _ports_from_lsof()
    if not candidates:
        return []
    found = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, len(candidates))
    ) as pool:
        futures = [pool.submit(_probe, port, owner) for port, owner in candidates]
        for future in concurrent.futures.as_completed(futures):
            try:
                candidate = future.result()
            except (OSError, ValueError):
                candidate = None
            if candidate:
                found.append(candidate)
            if len(found) >= _MAX_DISCOVERIES:
                break
    return found[:_MAX_DISCOVERIES]


@dataclass
class _Managed:
    browser_id: str
    session: BrowserSession
    ownership: str


class BrowserWorkspace:
    """Own zero or more browser sessions and delegate page tools to the active one."""

    _LIFECYCLE_TOOLS: ClassVar[set[str]] = {
        "list_browsers",
        "launch_browser",
        "attach_browser",
        "switch_browser",
        "detach_browser",
    }

    def __init__(self, profiles_dir: Path, *, headless=False):
        self.profiles_dir = Path(profiles_dir).resolve()
        self.headless = headless
        self._artifact_dir = self.profiles_dir.parent / "evidence"
        self._managed: dict[str, _Managed] = {}
        self.active_id: str | None = None
        self._observation: dict | None = None
        self._closed = False

    @property
    def profile_name(self):
        active = self._active()
        return active.session.profile.name if active else "workspace"

    @property
    def artifact_dir(self):
        return self._artifact_dir

    @artifact_dir.setter
    def artifact_dir(self, value):
        self._artifact_dir = Path(value).resolve()
        for managed in self._managed.values():
            managed.session.artifact_dir = self._artifact_dir

    @property
    def current_url(self):
        active = self._active()
        return active.session.page.url if active and active.session.page else None

    def _active(self):
        managed = self._managed.get(self.active_id or "")
        if managed is None:
            return None
        if not managed.session.is_open:
            return None
        return managed

    def _new_id(self):
        return "b-" + uuid4().hex[:14]

    async def _summary(self, managed):
        tabs = await managed.session.tabs_summary(limit=_MAX_TABS)
        tabs = [
            {
                "page_id": str(tab.get("page_id", ""))[:40],
                "title": str(tab.get("title", ""))[:120],
                "url": str(tab.get("url", ""))[:400],
                "active": bool(tab.get("active")),
            }
            for tab in tabs
        ]
        return {
            "browser_id": managed.browser_id,
            "ownership": managed.ownership,
            "active": managed.browser_id == self.active_id,
            "tabs": tabs,
        }

    async def _managed_summaries(self):
        summaries = []
        size = 0
        for managed in list(self._managed.values())[:_MAX_DISCOVERIES]:
            summary = await self._summary(managed)
            candidate_size = len(json.dumps(summary, ensure_ascii=False))
            if summaries and size + candidate_size > _MAX_METADATA_CHARS:
                break
            summaries.append(summary)
            size += candidate_size
        return summaries

    @staticmethod
    def _public_discoveries(discovered):
        public = []
        size = 0
        for item in discovered[:_MAX_DISCOVERIES]:
            candidate = {k: v for k, v in item.items() if not k.startswith("_")}
            candidate_size = len(json.dumps(candidate, ensure_ascii=False))
            if public and size + candidate_size > _MAX_METADATA_CHARS:
                break
            public.append(candidate)
            size += candidate_size
        return public

    async def discover(self):
        """Probe only current local Chromium debug ports; never cache endpoints."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(_discover_sync), 2)
        except TimeoutError:
            return []

    async def list_browsers(self):
        """Return fresh discovery plus browsers already managed by this workspace."""
        return await self._execute_lifecycle("list_browsers", {})

    async def launch_browser(self):
        """Launch and activate an owned browser without an initial URL."""
        return await self._launch()

    async def attach_browser(self, discovery_id):
        """Freshly rediscover and attach one observed local browser."""
        return await self._attach(discovery_id)

    async def switch_browser(self, browser_id):
        """Activate one currently managed browser by its observed ID."""
        result = await self._execute_lifecycle(
            "switch_browser", {"browser_id": browser_id}
        )
        return result["browser"]

    async def detach_browser(self):
        """Disconnect the active attached browser, leaving its external process open."""
        return await self._execute_lifecycle("detach_browser", {})

    async def prepare_task(self):
        active = self._active()
        if active:
            await active.session.prepare_task()
        else:
            self._observation = None

    async def observe(self, offset=0, scope=None):
        active = self._active()
        if active:
            observation = await active.session.observe(offset=offset, scope=scope)
            observation = dict(observation)
            observation["workspace"] = await self._managed_summaries()
            observation["active_browser"] = self.active_id
            self._observation = observation
            return observation
        if offset or scope:
            raise BrowserError(
                "no_active_browser", "There is no active browser to continue reading"
            )
        self._observation = {
            "id": "workspace-" + uuid4().hex[:16],
            "generation": "workspace",
            "revision": 0,
            "url": None,
            "title": "No active browser",
            "text": "No browser is active. Choose a local browser, launch an owned browser, or attach a discovered browser if the task needs one.",
            "refs": [],
            "truncated": False,
            "next_offset": None,
            "offset": 0,
            "tabs": [],
            "workspace": await self._managed_summaries(),
            "active_browser": None,
            "no_browser": True,
        }
        return dict(self._observation)

    def _check_workspace_observation(self, observation_id):
        if not self._observation or self._observation["id"] != observation_id:
            raise BrowserError("stale_observation", "Workspace observation is no longer current")
        active = self._active()
        if active:
            active.session._check_observation(observation_id)

    def _lifecycle_context(self, tool, args, observation_id):
        self._check_workspace_observation(observation_id)
        context = {
            "tag": "browser_workspace",
            "role": "browser",
            "type": "",
            "name": tool,
            "text": "",
            "href": "",
            "disabled": False,
            "attached": True,
            "visible": True,
            "context_complete": True,
            "context": json.dumps(
                {
                    "active_browser": self.active_id,
                    "browser_ids": list(self._managed)[:_MAX_DISCOVERIES],
                }
            ),
            "fields": [],
            "document_url": self.current_url or "",
            "action": {"tool": tool, "args": args},
        }
        context["fingerprint"] = hashlib.sha256(
            json.dumps(
                {
                    "active": self.active_id,
                    "managed": list(self._managed),
                    "tool": tool,
                    "args": args,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return context

    async def action_context(self, tool, args, observation_id):
        if tool in self._LIFECYCLE_TOOLS:
            return self._lifecycle_context(tool, args, observation_id)
        active = self._active()
        if active is None:
            raise BrowserError(
                "no_active_browser",
                "No browser is active; choose one with list_browsers, launch_browser, or attach_browser",
            )
        return await active.session.action_context(tool, args, observation_id)

    async def _launch(self):
        browser_id = self._new_id()
        has_owned = any(item.ownership == "owned" for item in self._managed.values())
        profile = self.profiles_dir / (browser_id if has_owned else "default")
        session = BrowserSession(
            profile,
            headless=self.headless,
            artifact_dir=self._artifact_dir,
        )
        try:
            await session.start()
        except Exception:
            await session.close()
            raise
        managed = _Managed(browser_id, session, "owned")
        self._managed[browser_id] = managed
        self.active_id = browser_id
        return await self._summary(managed)

    async def _attach(self, discovery_id):
        candidates = await self.discover()
        candidate = next(
            (item for item in candidates if item["discovery_id"] == discovery_id), None
        )
        if candidate is None:
            raise BrowserError(
                "unknown_discovery",
                "That browser discovery is stale or no longer connectable; list browsers again",
            )
        browser_id = self._new_id()
        session = BrowserSession(
            self.profiles_dir / ("attached-" + browser_id),
            artifact_dir=self._artifact_dir,
        )
        try:
            await session.attach(candidate["_endpoint"])
        except Exception:
            await session.close()
            raise
        managed = _Managed(browser_id, session, "attached")
        self._managed[browser_id] = managed
        self.active_id = browser_id
        return await self._summary(managed)

    async def _execute_lifecycle(self, tool, args):
        if tool == "list_browsers":
            discovered = await self.discover()
            return {
                "status": "executed",
                "tool": tool,
                "active_browser": self.active_id,
                "browsers": await self._managed_summaries(),
                "discovered": self._public_discoveries(discovered),
                "requires_observation": True,
            }
        if tool == "launch_browser":
            return {
                "status": "executed",
                "tool": tool,
                "browser": await self._launch(),
                "requires_observation": True,
            }
        if tool == "attach_browser":
            return {
                "status": "executed",
                "tool": tool,
                "browser": await self._attach(args["discovery_id"]),
                "requires_observation": True,
            }
        if tool == "switch_browser":
            browser_id = args["browser_id"]
            managed = self._managed.get(browser_id)
            if managed is None or not managed.session.is_open:
                raise BrowserError("unknown_browser", "Browser ID is not currently open")
            if managed.session.page is not None:
                await managed.session.bring_to_front()
            self.active_id = browser_id
            return {
                "status": "executed",
                "tool": tool,
                "browser": await self._summary(managed),
                "requires_observation": True,
            }
        if tool == "detach_browser":
            managed = self._active()
            if managed is None:
                raise BrowserError("no_active_browser", "No active browser to detach")
            if managed.ownership != "attached":
                raise BrowserError(
                    "owned_browser",
                    "Owned browsers stay managed until /exit; switch browsers or end the session",
                )
            await managed.session.close()
            self._managed.pop(managed.browser_id, None)
            self.active_id = None
            return {
                "status": "executed",
                "tool": tool,
                "browser_id": managed.browser_id,
                "requires_observation": True,
            }
        raise BrowserError("unknown_tool", "Unsupported browser lifecycle tool")

    async def execute(self, tool, args, observation_id, expected_fingerprint=None):
        if tool in self._LIFECYCLE_TOOLS:
            context = await self.action_context(tool, args, observation_id)
            if expected_fingerprint and context["fingerprint"] != expected_fingerprint:
                raise BrowserError(
                    "approval_changed", "Workspace action changed; choose it again"
                )
            return await self._execute_lifecycle(tool, args)
        active = self._active()
        if active is None:
            raise BrowserError(
                "no_active_browser",
                "No browser is active; choose one with list_browsers, launch_browser, or attach_browser",
            )
        return await active.session.execute(
            tool, args, observation_id, expected_fingerprint=expected_fingerprint
        )

    async def screenshot(self):
        active = self._active()
        if active is None:
            raise BrowserError("no_active_browser", "No active browser to screenshot")
        return await active.session.screenshot()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(
            *(managed.session.close() for managed in self._managed.values()),
            return_exceptions=True,
        )
        self._managed.clear()
        self.active_id = None
        self._observation = None
