"""Browser-JS line coverage from the Playwright run, written as LCOV for SonarCloud.

pytest measures Python only, so before this the browser code (flaskapp/static/js)
counted as 0% covered although these tests execute it in a real Chromium. This
records what actually ran, through Chromium's own V8 coverage (the DevTools
`Profiler` domain), and needs no build step or instrumented copy of the JS:
the browser runs the exact files the app serves.

Off unless E2E_JS_COVERAGE names an output file, so a local `pytest tests/e2e`
behaves exactly as before. Chromium only.

Why a second DevTools connection on its own thread, polling, rather than
Playwright's `new_cdp_session` read once at the end. Each found by checking
the report against code no test reaches (the admin dashboard):

* A navigation throws away the old page's coverage. It cannot be taken "just
  before" a navigation either: with the document request paused, the old
  renderer does not answer until the request is released. So coverage is taken
  every POLL_SECONDS from every page, which Playwright's synchronous API cannot
  do in the background. Each take resets V8's counters, so nothing is counted
  twice; the most that can be lost is what ran in the last fraction of a second
  before a page load, which can only under-report.
* A page must be measured from its first script, or V8 reports a script as one
  whole-file range ("every line ran"). New pages are therefore held at start
  (`waitForDebuggerOnStart`) until coverage is on, and `Debugger.enable` keeps
  block-level detail for scripts loaded again from V8's cache.

How a line is scored: V8 reports nested ranges with an execution count
(function, then blocks inside it). The innermost range wins, so a line's count
is the count at its first non-blank character. Counts are summed across every
page and test. Every non-blank line is reported, so SonarCloud's own idea of an
executable line decides what is counted.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import threading
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

STATIC_JS = Path(__file__).resolve().parents[2] / "flaskapp" / "static" / "js"
REPO_ROOT = STATIC_JS.parents[2]
SERVED_PREFIX = "/static/js/"
TIMEOUT_SECONDS = 15
POLL_SECONDS = 0.1



class JsCoverage:
    """Owns a DevTools connection to the test browser and the merged line hits."""

    def __init__(self, debugging_port: int) -> None:
        # repo-relative path -> {line number: hit count}
        self.hits: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._port = debugging_port
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._pages: set[str] = set()  # session ids of attached pages
        self.skipped_reports = 0  # reports without block detail, see _add
        self._lock = threading.Lock()
        self._taking: asyncio.Lock | None = None
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="js-coverage", daemon=True)

    # ---- called from the test thread ---------------------------------------

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(TIMEOUT_SECONDS):
            raise RuntimeError("JS coverage: could not attach to the browser")
        if self._error is not None:
            raise RuntimeError("JS coverage: could not attach to the browser") from self._error

    def flush(self) -> None:
        """Take coverage from every open page (call before a context closes)."""
        asyncio.run_coroutine_threadsafe(self._take_all(), self._loop).result(TIMEOUT_SECONDS)

    def write_lcov(self, output: Path) -> None:
        records = []
        with self._lock:
            for relative in sorted(self.hits):
                lines = self.hits[relative]
                records.append(f"SF:{relative}")
                records += [f"DA:{n},{lines[n]}" for n in sorted(lines)]
                records.append(f"LF:{len(lines)}")
                records.append(f"LH:{sum(1 for n in lines if lines[n] > 0)}")
                records.append("end_of_record")
        output.write_text("\n".join(records) + "\n", encoding="utf-8")

    # ---- the coverage thread -----------------------------------------------

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        try:
            with urllib.request.urlopen(  # loopback only: the port we opened
                f"http://127.0.0.1:{self._port}/json/version", timeout=TIMEOUT_SECONDS
            ) as response:
                endpoint = json.load(response)["webSocketDebuggerUrl"]
            import websockets  # only when measuring; a transitive dependency

            self._socket = await websockets.connect(endpoint, max_size=None)
            reader = asyncio.ensure_future(self._read())
            await self._send("Target.setAutoAttach", {
                "autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
            })
        except BaseException as error:  # surfaced to the test thread by start()
            self._error = error
            self._ready.set()
            return
        self._taking = asyncio.Lock()
        self._ready.set()
        while not reader.done():
            await asyncio.sleep(POLL_SECONDS)
            await self._take_all()

    async def _read(self) -> None:
        try:
            await self._dispatch()
        except Exception:  # the browser closed at the end of the run
            return

    async def _dispatch(self) -> None:
        async for raw in self._socket:
            message = json.loads(raw)
            future = self._pending.pop(message.get("id"), None)
            if future is not None:
                future.set_result(message)
            elif "method" in message:
                asyncio.ensure_future(self._on_event(message))

    async def _send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        message_id = next(self._ids)
        future = self._loop.create_future()
        self._pending[message_id] = future
        message = {"id": message_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self._socket.send(json.dumps(message))
        reply = await asyncio.wait_for(future, TIMEOUT_SECONDS)
        return reply.get("result", {})

    async def _on_event(self, message: dict) -> None:
        method, params = message["method"], message.get("params", {})
        session = message.get("sessionId")
        if method == "Target.attachedToTarget":
            await self._on_attached(params)
        elif method == "Target.detachedFromTarget":
            self._pages.discard(params.get("sessionId"))

    async def _on_attached(self, params: dict) -> None:
        session = params["sessionId"]
        if params["targetInfo"]["type"] != "page":
            await self._send("Runtime.runIfWaitingForDebugger", session=session)
            return
        for method, args in (
            ("Debugger.enable", {}),
            ("Debugger.setSkipAllPauses", {"skip": True}),
            ("Profiler.enable", {}),
            ("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True}),
        ):
            await self._send(method, args, session)
        self._pages.add(session)
        await self._send("Runtime.runIfWaitingForDebugger", session=session)

    async def _take_all(self) -> None:
        async with self._taking:
            for session in list(self._pages):
                await self._take(session)

    async def _take(self, session: str) -> None:
        # Taking coverage also resets V8's counters, so nothing is counted twice.
        try:
            result = (await self._send("Profiler.takePreciseCoverage", session=session))["result"]
        except Exception:  # page gone, or busy past the timeout: try again next poll
            return
        for script in result:
            self._add(script)

    def _add(self, script: dict) -> None:
        path = urlsplit(script.get("url", "")).path
        if not path.startswith(SERVED_PREFIX):
            return
        source_file = STATIC_JS / path[len(SERVED_PREFIX):]
        if source_file.suffix != ".js" or not source_file.is_file():
            return
        top_level = next((f for f in script["functions"]
                          if f["ranges"] and f["ranges"][0]["startOffset"] == 0), None)
        if top_level is not None and not top_level["isBlockCoverage"]:
            # A whole-file range with no block detail would mark every line
            # as run. Dropping the report can only under-report.
            self.skipped_reports += 1
            return
        source = source_file.read_text(encoding="utf-8")
        relative = source_file.relative_to(REPO_ROOT).as_posix()
        counts = line_counts(source, script["functions"])
        with self._lock:
            for line, count in counts.items():
                self.hits[relative][line] += count


def line_counts(source: str, functions: list[dict]) -> dict[int, int]:
    """Execution count per non-blank line (1-based) from V8 block coverage."""
    # V8 offsets count UTF-16 code units; Python indexes code points.
    unit_to_char: list[int] = []
    for index, char in enumerate(source):
        unit_to_char.extend([index] * (2 if ord(char) > 0xFFFF else 1))
    unit_to_char.append(len(source))

    counts = [0] * len(source)
    ranges = sorted(
        (r for function in functions for r in function["ranges"]),
        key=lambda r: (r["startOffset"], -r["endOffset"]),
    )
    # Outer ranges first, inner ones later, so the innermost count wins.
    for r in ranges:
        start = unit_to_char[min(r["startOffset"], len(unit_to_char) - 1)]
        end = unit_to_char[min(r["endOffset"], len(unit_to_char) - 1)]
        counts[start:end] = [r["count"]] * (end - start)

    result: dict[int, int] = {}
    offset = 0
    for number, line in enumerate(source.split("\n"), start=1):
        first = re.search(r"\S", line)
        if first is not None:
            result[number] = counts[offset + first.start()]
        offset += len(line) + 1
    return result
