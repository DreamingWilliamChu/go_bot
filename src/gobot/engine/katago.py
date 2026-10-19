"""Persistent KataGo analysis-engine process management.

One ``katago analysis`` process is kept alive and spoken to over its documented JSON-lines
protocol (docs/Analysis_Engine.md upstream). Responsibilities:

* unique request ids, routing of partial/final responses to the right waiter
* cancellation via ``{"action": "terminate"}``
* per-request timeouts (terminate, then restart the engine if it stays silent)
* stale-result suppression: a newer request from the same client supersedes older ones
* crash detection, actionable error reporting and restart on the next request
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import itertools
import json
import logging
import re
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gobot.engine.query import AnalyzeParams, Position, build_query, normalize_response

log = logging.getLogger("gobot.engine")

STDOUT_LIMIT = 64 * 1024 * 1024  # analysis lines with ownership can be large


class EngineError(RuntimeError):
    """The engine failed (crashed, hung, could not start)."""


class EngineUnavailable(EngineError):
    """Binary or model missing; the message tells the user how to fix it."""


class QueryError(RuntimeError):
    """KataGo rejected a query (e.g. an illegal move)."""


@dataclass
class _Ended:
    error: EngineError


@dataclass
class EngineSpec:
    binary: str | None
    model_path: Path | None
    config_path: Path
    model_id: str | None = None
    model_label: str | None = None
    startup_timeout: float = 600.0  # first Metal/CoreML start can be slow


def probe_binary(binary: str, timeout: float = 30.0) -> dict[str, Any]:
    """Run ``katago version`` and parse version and backend."""
    try:
        out = subprocess.run(
            [binary, "version"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise EngineUnavailable(f"KataGo executable not found at {binary!r}") from e
    except OSError as e:
        raise EngineUnavailable(f"Cannot run KataGo at {binary!r}: {e}") from e
    text = (out.stdout or "") + "\n" + (out.stderr or "")
    version = re.search(r"KataGo v?([0-9][\w.\-+]*)", text)
    backend = re.search(r"Using (.+?) backend", text)
    git = re.search(r"Git revision:\s*(\S+)", text)
    backend_name = backend.group(1).strip() if backend else "unknown"
    return {
        "version": version.group(1) if version else None,
        "backend": backend_name,
        "backend_kind": backend_kind(backend_name),
        "git_revision": git.group(1) if git else None,
        "raw": text.strip()[-2000:],
    }


def backend_kind(name: str) -> str:
    n = name.lower()
    for kind in ("metal", "eigen", "cuda", "tensorrt", "opencl", "rocm", "onnx", "coreml"):
        if kind in n:
            return kind
    return "other"


class KataGoProcess:
    """A single running ``katago analysis`` process."""

    def __init__(self, spec: EngineSpec):
        self.spec = spec
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "stopped"
        self.version: str | None = None
        self.git_hash: str | None = None
        self.model_internal_name: str | None = None
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=300)
        self.model_desc: str | None = None
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.exit_code: int | None = None
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._actions: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self._tasks: list[asyncio.Task[None]] = []
        self._ended: EngineError | None = None

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        s = self.spec
        if not s.binary:
            raise EngineUnavailable(
                "KataGo is not installed. On the Mac run: brew install katago (or ./scripts/setup_mac.sh)."
            )
        if s.model_path is None or not Path(s.model_path).is_file():
            raise EngineUnavailable(
                f"Neural network file not found ({s.model_path or s.model_id or 'no model selected'}). "
                "Run ./scripts/download_models.sh to download the KataGo networks."
            )
        if not Path(s.config_path).is_file():
            raise EngineUnavailable(f"KataGo config not found: {s.config_path}")
        self.state = "starting"
        self.started_at = time.monotonic()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                s.binary,
                "analysis",
                "-config",
                str(s.config_path),
                "-model",
                str(s.model_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STDOUT_LIMIT,
            )
        except OSError as e:
            self.state = "dead"
            raise EngineUnavailable(f"Cannot start KataGo ({s.binary}): {e}") from e
        self._tasks = [
            asyncio.create_task(self._read_stdout(), name="katago-stdout"),
            asyncio.create_task(self._read_stderr(), name="katago-stderr"),
        ]
        try:
            v = await self.action({"action": "query_version"}, timeout=s.startup_timeout)
            self.version = v.get("version")
            self.git_hash = v.get("git_hash")
            with contextlib.suppress(EngineError, QueryError, asyncio.TimeoutError):
                m = await self.action({"action": "query_models"}, timeout=30)
                models = m.get("models") or []
                if models:
                    self.model_internal_name = models[0].get("internalName") or models[0].get("name")
        except (TimeoutError, EngineError) as e:
            tail = self.tail_text()
            await self.close(force=True)
            self.state = "dead"
            raise EngineError(f"KataGo failed to start: {e}\n{tail}") from e
        self.state = "ready"
        self.ready_at = time.monotonic()

    async def close(self, force: bool = False) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.returncode is None:
            if not force:
                with contextlib.suppress(Exception):
                    await self._write({"id": "shutdown", "action": "terminate_all"})
            with contextlib.suppress(Exception):
                if proc.stdin:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5 if force else 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
        for t in self._tasks:
            t.cancel()
        self._fail_all(EngineError("KataGo was stopped"))
        self.state = "stopped" if not force else "dead"

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and self._ended is None

    def tail_text(self, n: int = 25) -> str:
        return "\n".join(list(self.stderr_tail)[-n:])

    # ---------------------------------------------------------------- io
    async def _write(self, obj: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None or not self.alive:
            raise self._ended or EngineError("KataGo is not running")
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            try:
                self.proc.stdin.write(line)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as e:
                raise EngineError("KataGo input pipe closed (engine crashed?)") from e

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                raw = await self.proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Some builds print non-JSON notices on stdout; keep them for diagnostics.
                    self.stderr_tail.append(f"[stdout] {line[:500]}")
                    continue
                self._dispatch(obj)
        except Exception as e:  # pragma: no cover - defensive
            log.exception("stdout reader failed")
            self.stderr_tail.append(f"[gobot] stdout reader error: {e}")
        finally:
            code = None
            if self.proc is not None:
                with contextlib.suppress(Exception):
                    code = await asyncio.wait_for(self.proc.wait(), timeout=5)
            self.exit_code = code
            if self.state != "stopped":
                self.state = "dead"
            msg = f"KataGo exited unexpectedly (exit code {code})."
            tail = self.tail_text(12)
            if tail:
                msg += f" Last log lines:\n{tail}"
            self._ended = EngineError(msg)
            self._fail_all(self._ended)

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            raw = await self.proc.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            self.stderr_tail.append(line)
            m = re.search(r"Model name: (.+)$", line)
            if m and not self.model_desc:
                self.model_desc = m.group(1).strip()

    def _dispatch(self, obj: dict[str, Any]) -> None:
        qid = obj.get("id")
        if "action" in obj and qid in self._actions:
            fut = self._actions.pop(qid)
            if not fut.done():
                fut.set_result(obj)
            return
        if qid is not None and qid in self._queues:
            self._queues[qid].put_nowait(obj)
            return
        if qid is not None and qid in self._actions and "error" in obj:
            fut = self._actions.pop(qid)
            if not fut.done():
                fut.set_exception(QueryError(obj["error"]))
            return
        if "error" in obj:
            self.stderr_tail.append(f"[stdout error] {json.dumps(obj)[:500]}")
        # Responses for queries nobody waits for anymore are dropped deliberately.

    def _fail_all(self, err: EngineError) -> None:
        for q in self._queues.values():
            q.put_nowait(_Ended(err))
        for fut in self._actions.values():
            if not fut.done():
                fut.set_exception(err)
        self._actions.clear()

    # ---------------------------------------------------------------- requests
    def new_id(self, prefix: str = "a") -> str:
        return f"{prefix}{next(self._ids)}"

    async def action(self, obj: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        qid = obj.get("id") or self.new_id("act")
        obj = {**obj, "id": qid}
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._actions[qid] = fut
        try:
            await self._write(obj)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._actions.pop(qid, None)

    async def submit(self, query: dict[str, Any]) -> asyncio.Queue[Any]:
        qid = query["id"]
        if qid in self._queues:
            raise ValueError(f"duplicate query id {qid}")
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._queues[qid] = q
        try:
            await self._write(query)
        except Exception:
            self._queues.pop(qid, None)
            raise
        return q

    def release(self, qid: str) -> None:
        self._queues.pop(qid, None)

    async def terminate(self, qid: str) -> None:
        with contextlib.suppress(EngineError):
            await self._write({"id": self.new_id("term"), "action": "terminate", "terminateId": qid})


@dataclass
class _Active:
    qid: str
    cancelled: bool = False


@dataclass
class EngineManager:
    """Owns the KataGo process and serialises analysis requests per client."""

    spec_factory: Any  # callable(model_id: str | None) -> EngineSpec
    timeout_grace: float = 15.0
    hang_grace: float = 15.0
    fallback_chain: tuple[str, ...] = ()
    proc: KataGoProcess | None = None
    probe: dict[str, Any] = field(default_factory=dict)
    model_id: str | None = None
    last_error: str | None = None
    fallback_note: str | None = None
    restarts: int = 0
    _start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _active: dict[str, _Active] = field(default_factory=dict)
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        p = self.proc
        spec: EngineSpec | None = p.spec if p else None
        return {
            "state": p.state if p else ("error" if self.last_error else "stopped"),
            "modelId": self.model_id,
            "modelLabel": spec.model_label if spec else None,
            "modelFile": Path(spec.model_path).name if spec and spec.model_path else None,
            "modelInternalName": p.model_internal_name if p else None,
            "modelDescription": p.model_desc if p else None,
            "version": (p.version if p and p.version else self.probe.get("version")),
            "gitHash": p.git_hash if p else None,
            "backend": self.probe.get("backend"),
            "backendKind": self.probe.get("backend_kind"),
            "binary": spec.binary if spec else None,
            "error": self.last_error,
            "fallbackNote": self.fallback_note,
            "restarts": self.restarts,
            "startupSeconds": (
                round(p.ready_at - p.started_at, 2) if p and p.ready_at and p.started_at else None
            ),
        }

    # ---------------------------------------------------------------- lifecycle
    async def ensure_started(self, model_id: str | None = None) -> KataGoProcess:
        async with self._start_lock:
            want = model_id if model_id is not None else self.model_id
            if self.proc and self.proc.alive and self.proc.state == "ready" and want == self.model_id:
                return self.proc
            if self.proc is not None:
                await self.proc.close(force=not self.proc.alive)
                self.proc = None
                self.restarts += 1
            candidates = [want] + [m for m in self.fallback_chain if m != want]
            errors: list[str] = []
            for i, mid in enumerate(candidates):
                if i > 0 and not self.fallback_chain:
                    break
                spec = self.spec_factory(mid)
                if spec.binary and not self.probe:
                    try:
                        self.probe = await asyncio.to_thread(probe_binary, spec.binary)
                    except EngineUnavailable as e:
                        self.last_error = str(e)
                        raise
                proc = KataGoProcess(spec)
                try:
                    await proc.start()
                except EngineUnavailable as e:
                    errors.append(f"{mid or spec.model_path}: {e}")
                    if i == 0 and not spec.binary:
                        self.last_error = str(e)
                        raise
                    continue
                except EngineError as e:
                    errors.append(f"{mid or spec.model_path}: {e}")
                    continue
                self.proc = proc
                self.model_id = spec.model_id
                self.last_error = None
                self.fallback_note = (
                    f"Requested model {want!r} failed; using {spec.model_id!r} instead. " + " | ".join(errors)
                    if i > 0
                    else None
                )
                return proc
            self.last_error = "; ".join(errors) or "KataGo could not be started"
            raise (
                EngineUnavailable(self.last_error)
                if all("not found" in e or "not installed" in e for e in errors)
                else EngineError(self.last_error)
            )

    async def restart(self, model_id: str | None = None) -> KataGoProcess:
        async with self._start_lock:
            if self.proc is not None:
                await self.proc.close(force=True)
                self.proc = None
                self.restarts += 1
        return await self.ensure_started(model_id)

    async def shutdown(self) -> None:
        if self.proc is not None:
            await self.proc.close()
            self.proc = None

    # ---------------------------------------------------------------- analysis
    def is_current(self, client: str, qid: str) -> bool:
        a = self._active.get(client)
        return a is not None and a.qid == qid

    async def cancel(self, client: str, qid: str | None = None) -> bool:
        a = self._active.get(client)
        if a is None or (qid is not None and a.qid != qid):
            return False
        a.cancelled = True
        if self.proc is not None and self.proc.alive:
            await self.proc.terminate(a.qid)
        return True

    async def analyze(
        self, client: str, pos: Position, params: AnalyzeParams, top_n: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Analyse ``pos``; yields normalised partial results then exactly one final result.

        Starting a new analysis for the same ``client`` terminates the previous one; results of
        the superseded query are drained but never yielded (no stale overwrite).
        """
        proc = await self.ensure_started()
        qid = f"{client}-{next(self._seq)}"
        prev = self._active.get(client)
        me = _Active(qid)
        self._active[client] = me
        if prev is not None:
            await proc.terminate(prev.qid)
        query = build_query(qid, pos, params)
        queue = await proc.submit(query)
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + params.max_time + max(self.timeout_grace, params.max_time * 0.5)
        terminate_sent = False
        got_final = False
        try:
            while True:
                remaining = deadline - loop.time()
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=max(0.05, remaining))
                except TimeoutError:
                    if not terminate_sent:
                        terminate_sent = True
                        await proc.terminate(qid)
                        deadline = loop.time() + self.hang_grace
                        continue
                    # No reply even after terminate: the engine is hung. Restart it.
                    self.last_error = "KataGo stopped responding; it was restarted."
                    await self.restart()
                    raise EngineError(
                        f"KataGo did not answer within {params.max_time + self.timeout_grace + self.hang_grace:.0f}s; "
                        "the engine was restarted. Try again."
                    ) from None
                if isinstance(item, _Ended):
                    self.last_error = str(item.error)
                    raise item.error
                if "error" in item:
                    field_name = item.get("field")
                    raise QueryError(item["error"] + (f" (field: {field_name})" if field_name else ""))
                if "warning" in item:
                    if self.is_current(client, qid):
                        yield {
                            "type": "warning",
                            "id": qid,
                            "message": item["warning"],
                            "field": item.get("field"),
                        }
                    continue
                final = not item.get("isDuringSearch", False)
                if self.is_current(client, qid):
                    res = normalize_response(item, pos.size, top_n=top_n)
                    res["type"] = "final" if final else "partial"
                    res["elapsed"] = round(loop.time() - started, 3)
                    res["cancelled"] = me.cancelled
                    res["timedOut"] = terminate_sent and not me.cancelled
                    yield res
                if final:
                    got_final = True
                    return
        finally:
            if not got_final and proc.alive:
                # Consumer went away (e.g. websocket closed): stop the search.
                with contextlib.suppress(Exception):
                    await proc.terminate(qid)
            proc.release(qid)
            if self._active.get(client) is me:
                del self._active[client]
