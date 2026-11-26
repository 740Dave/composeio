"""Running a plug-in: in-process, or through the SUBPROCESS RUNNER
([CXC-PC-SUBPROCESS-RUNNER], [CXC-PC-RUNNER-LOCAL-ONLY]).

THE LINK. The host creates an ``AF_UNIX`` ``socketpair()``; the child inherits
one end (``pass_fds``). Nothing listens and no path exists. If
``socket.socketpair()`` does not yield ``AF_UNIX`` the runner REFUSES TO START
and never falls back to TCP.

FRAMES. Newline-delimited JSON. A BUS frame is ``BusMessage.to_json()``. A
CONTROL frame has a top-level ``_ctl``: host to child ``configure``, ``start``,
``stop``; child to host ``health`` (``state: running``) and ``degraded``
(``reason``). Any other ``_ctl`` value, or a line that is not one JSON object,
is refused loudly: the plug-in becomes ``failed(protocol: ...)`` and the child
is killed.

THE BOUNDARY. Only bus frames for the manifest's declared publishes
``(namespace, TYPE)`` are applied to the host bus; anything else is dropped and
counted in ``refused_frames``. Only the declared subscribes reach the child.

CRASHES. EOF from the child, a non-zero exit, a signal, or no start handshake
within ``start_timeout`` is ``failed(<reason>)`` naming the exit code or
signal. The reader is a daemon thread that catches everything, so the host's
own loop never dies with the child.

Health ([CXC-PC-LIFECYCLE-HEALTH], r15): a plug-in MAY define
``health_reason(self) -> str | None``; non-None means ``degraded(<reason>)``.
In-process the host polls it after start and on each tick; in the child, the
child polls it and sends ``_ctl: degraded`` / ``_ctl: health``.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from composebus.transport import BusMessage, MessageType

from .errors import PluginContractRefusal

CTL_TO_CHILD = ("configure", "start", "stop")
CTL_TO_HOST = ("health", "degraded")
CTL_VALUES = CTL_TO_CHILD + CTL_TO_HOST
HOST_SOURCE = "_host"


class RunnerRefused(PluginContractRefusal):
    """The runner refuses to start (for example: no AF_UNIX socketpair)."""


def make_socketpair() -> "tuple[socket.socket, socket.socket]":
    """The host's end and the child's end, AF_UNIX or a refusal."""
    a, b = socket.socketpair()
    if a.family != socket.AF_UNIX or b.family != socket.AF_UNIX:
        fam = a.family
        a.close()
        b.close()
        raise RunnerRefused(f"subprocess runner REFUSES TO START: socket.socketpair() "
                            f"gave {fam!r}, not AF_UNIX; it never falls back to TCP "
                            "([CXC-PC-RUNNER-LOCAL-ONLY])")
    return a, b


def frame_from_message(msg: BusMessage) -> bytes:
    return (json.dumps(msg.to_json(), separators=(",", ":")) + "\n").encode("utf-8")


def ctl_frame(value: str, **payload: Any) -> bytes:
    if value not in CTL_VALUES:
        raise RunnerRefused(f"_ctl {value!r} is not one of {list(CTL_VALUES)}")
    return (json.dumps({"_ctl": value, **payload}, separators=(",", ":")) + "\n").encode()


def apply_to_bus(bus, msg: BusMessage, source: str) -> None:
    """Replay a bus frame onto ``bus`` with ``source``."""
    if msg.type is MessageType.STATE:
        bus.publish_state(msg.namespace, msg.value, source=source)
    elif msg.type is MessageType.EVENT:
        bus.publish_event(msg.namespace, msg.event_type, dict(msg.data or {}), source=source)


StatusSink = Callable[[str], None]


#: the library's own health-poll period for an in-process plug-in (the
#: subprocess child polls at its own cadence); a host may also call tick()
HEALTH_POLL_S = 0.5


class _TrackingBus:
    """The bus as an in-process plug-in sees it: every call passes through, and
    every subscription is RECORDED so ``stop`` can remove them. composebus has
    no bus-level event unsubscribe, so event callbacks are removed from the
    namespace's subscriber list directly."""

    def __init__(self, bus):
        self._bus = bus
        self._subs: "list[tuple[str, str, Any]]" = []

    def __getattr__(self, name: str):
        return getattr(self._bus, name)

    def subscribe(self, namespace, callback):
        self._subs.append(("state", namespace, callback))
        return self._bus.subscribe(namespace, callback)

    def subscribe_events(self, namespace, callback):
        self._subs.append(("event", namespace, callback))
        return self._bus.subscribe_events(namespace, callback)

    def subscribe_pattern(self, pattern, callback):
        self._subs.append(("pattern", pattern, callback))
        return self._bus.subscribe_pattern(pattern, callback)

    def on_message(self, callback):
        self._subs.append(("message", "", callback))
        return self._bus.on_message(callback)

    def unsubscribe_all(self) -> None:
        for kind, name, cb in reversed(self._subs):
            try:
                if kind == "pattern":
                    self._bus.unsubscribe_pattern(name, cb)
                elif kind == "message":
                    self._bus.off_message(cb)
                else:
                    ns = self._bus.get_namespace(name)
                    if ns is None:
                        continue
                    lst = ns._subscribers if kind == "state" else ns._event_subscribers
                    if cb in lst:
                        lst.remove(cb)
            except Exception:
                pass
        self._subs.clear()


class InprocessPlugin:
    """A plug-in instance running in the host process, on the host's bus.

    ``health_reason()`` is polled after start, on every ``tick()`` and by the
    library's own poll every ``HEALTH_POLL_S`` ([CXC-PC-LIFECYCLE-HEALTH]), so a
    plug-in that turns unwell after start is reported without the host having to
    drive a tick. ``stop`` removes every subscription the instance made."""

    level = "inprocess"

    def __init__(self, cls: type, config: Any, bus, on_status: StatusSink,
                 poll_s: "float | None" = None):
        self.cls, self.config, self.on_status = cls, config, on_status
        self.poll_s = HEALTH_POLL_S if poll_s is None else poll_s   # read at run time
        self.bus = _TrackingBus(bus) if bus is not None else None
        self.instance = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: "str | None" = None

    def start(self) -> None:
        try:
            self.instance = self.cls(config=self.config)
            self.instance.start(self.bus)
        except Exception as exc:  # the host survives a plug-in that raises
            self.instance = None
            if self.bus is not None:
                self.bus.unsubscribe_all()
            self.on_status(f"failed(start raised {type(exc).__name__}: {exc})")
            return
        self.tick()
        threading.Thread(target=self._poll, daemon=True, name="inprocess-health").start()

    def _poll(self) -> None:
        while not self._stop.wait(self.poll_s):
            self.tick()

    def tick(self) -> None:
        with self._lock:
            inst = self.instance
            if inst is None:
                return
            probe = getattr(inst, "health_reason", None)
            try:
                reason = probe() if callable(probe) else None
                status = f"degraded({reason})" if reason is not None else "running"
            except Exception as exc:
                status = f"failed(health_reason raised {type(exc).__name__}: {exc})"
            if status != self._last:
                self._last = status
                self.on_status(status)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self.instance is not None:
                try:
                    self.instance.stop()
                finally:
                    self.instance = None
            if self.bus is not None:
                self.bus.unsubscribe_all()
            self._last = None
        self.on_status("stopped")

    @property
    def pid(self) -> "int | None":
        return None


class SubprocessPlugin:
    """A plug-in in a CHILD process, bridged to the host bus over the pair."""

    level = "subprocess"

    def __init__(self, *, domain: str, entry: str, sys_path: "list[str]", config: dict,
                 bus, publishes: "set[tuple[str, str]]", subscribes: "set[tuple[str, str]]",
                 on_status: StatusSink, start_timeout: float = 15.0,
                 socketpair: Callable[[], Any] = make_socketpair):
        self.domain, self.entry, self.sys_path, self.config = domain, entry, sys_path, config
        self.bus, self.publishes, self.subscribes = bus, publishes, subscribes
        self.on_status, self.start_timeout = on_status, start_timeout
        self._socketpair = socketpair
        self.tag = f"plugin:{domain}"
        self.proc: "subprocess.Popen | None" = None
        self.sock: "socket.socket | None" = None
        self.refused_frames: list[str] = []
        self.status = "stopped"
        self._wlock = threading.Lock()
        self._started = threading.Event()
        self._stopping = False
        self._reader: "threading.Thread | None" = None

    # ---- lifecycle -------------------------------------------------------- #
    def start(self) -> None:
        host_end, child_end = self._socketpair()   # refuses unless AF_UNIX
        if host_end.family != socket.AF_UNIX:      # belt and braces
            raise RunnerRefused("subprocess runner REFUSES TO START: link is not AF_UNIX")
        fd = child_end.fileno()
        os.set_inheritable(fd, True)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "compose_plugins._child", "--fd", str(fd)],
            pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, close_fds=True)
        child_end.close()
        self.sock = host_end
        self._rfile = host_end.makefile("rb")
        self._stopping = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name=f"runner-{self.domain}")
        self._reader.start()
        if self.bus is not None:
            self.bus.on_message(self._from_host_bus)
        self._send(ctl_frame("configure", domain=self.domain, entry=self.entry,
                             sys_path=self.sys_path, config=self.config,
                             publishes=sorted(self.publishes),
                             subscribes=sorted(self.subscribes)))
        self._send(ctl_frame("start"))
        if not self._started.wait(self.start_timeout) and not self.status.startswith("failed"):
            self._fail(f"no start handshake within {self.start_timeout}s")

    def tick(self) -> None:
        """Health arrives as frames; nothing to poll."""

    def stop(self) -> None:
        self._stopping = True
        if self.bus is not None:
            self.bus.off_message(self._from_host_bus)
        if self.proc is not None and self.proc.poll() is None:
            try:
                self._send(ctl_frame("stop"))
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._close()
        if not self.status.startswith("failed"):
            self._set("stopped")

    @property
    def pid(self) -> "int | None":
        return self.proc.pid if self.proc else None

    # ---- internals -------------------------------------------------------- #
    def _set(self, status: str) -> None:
        self.status = status
        self.on_status(status)

    def _fail(self, reason: str) -> None:
        if self.status.startswith("failed"):
            return
        self._set(f"failed({reason})")
        self._started.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def _close(self) -> None:
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass

    def _send(self, data: bytes) -> None:
        with self._wlock:
            if self.sock is None:
                return
            try:
                self.sock.sendall(data)
            except OSError as exc:
                if not self._stopping:
                    self._fail(f"link broken: {exc}")

    def _from_host_bus(self, msg: BusMessage) -> None:
        if msg.source == self.tag:
            return  # our own child's traffic: never echo it back
        if (msg.namespace, msg.type.name) in self.subscribes:
            self._send(frame_from_message(msg))

    def _read_loop(self) -> None:
        try:
            for raw in self._rfile:
                self._handle_line(raw)
                if self.status.startswith("failed"):
                    break
        except Exception as exc:  # the host's loop never dies with the child
            self._fail(f"reader error {type(exc).__name__}: {exc}")
        finally:
            self._on_eof()

    def _handle_line(self, raw: bytes) -> None:
        try:
            obj = json.loads(raw)
        except ValueError:
            self._fail(f"protocol: a line from the child is not JSON: {raw[:80]!r}")
            return
        if not isinstance(obj, dict):
            self._fail(f"protocol: a line from the child is not one JSON object: {raw[:80]!r}")
            return
        if "_ctl" in obj:
            ctl = obj["_ctl"]
            if ctl not in CTL_TO_HOST:
                self._fail(f"protocol: control frame _ctl={ctl!r} refused (child may "
                           f"send only {list(CTL_TO_HOST)})")
                return
            if ctl == "health":
                self._set("running")
                self._started.set()
            else:
                self._set(f"degraded({obj.get('reason')})")
                self._started.set()
            return
        try:
            msg = BusMessage.from_json(obj)
        except Exception as exc:
            self._fail(f"protocol: bad bus frame ({type(exc).__name__}: {exc})")
            return
        key = (msg.namespace, msg.type.name)
        if key not in self.publishes:
            self.refused_frames.append(f"{key[0]}:{key[1]}")
            return
        if self.bus is not None:
            apply_to_bus(self.bus, msg, self.tag)

    def _on_eof(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=5)
        if self._stopping and code == 0:
            return
        err = b""
        try:
            err = proc.stderr.read() if proc.stderr else b""
        except Exception:
            pass
        last = err.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        if code < 0:
            reason = f"killed by signal {signal.Signals(-code).name}"
        else:
            reason = f"exit code {code}"
        if last[0]:
            reason += f": {last[0]}"
        if not self._stopping or code != 0:
            self._fail(reason)
