"""hello-world v1 ([CXC-PC-HELLO-V1]).

Config ``greeting: str = "hello"``. Publishes STATE ``plugins/hello_world/greeting``
(the greeting text). Subscribes EVENT ``plugins/hello_world/ping``; each ping
publishes EVENT ``plugins/hello_world/pong`` with ``{greeting, n}``, where ``n``
counts pongs sent since THIS start, from 1 (a restart resets it).
``health_reason()`` returns "empty greeting" when ``greeting == ""``.
"""
from typing import ClassVar

from pydantic import BaseModel

from composebus.plugin import ComposePlugin

GREETING = "plugins/hello_world/greeting"
PING = "plugins/hello_world/ping"
PONG = "plugins/hello_world/pong"


class HelloWorld(ComposePlugin):
    class Config(BaseModel):
        greeting: str = "hello"

    name = "hello_world"
    version = "1.0.0"
    publishes = [GREETING, PONG]
    subscribes = [PING]
    bus_types: ClassVar[dict] = {GREETING: "STATE", PONG: "EVENT", PING: "EVENT"}

    def start(self, bus):
        self._bus = bus
        self._n = 0
        self._running = True
        bus.publish_state(GREETING, self.config.greeting, source="plugin:hello_world")
        bus.subscribe_events(PING, self._on_ping)

    def _pongs_per_ping(self) -> int:
        return 1

    def _on_ping(self, namespace, event_type, data):
        if not getattr(self, "_running", False):
            return  # a stopped instance never answers (no bus-level unsubscribe)
        for _ in range(self._pongs_per_ping()):
            self._n += 1
            self._bus.publish_event(PONG, "pong",
                                    {"greeting": self.config.greeting, "n": self._n},
                                    source="plugin:hello_world")

    def stop(self):
        self._running = False
        self._bus = None

    def health_reason(self):
        return "empty greeting" if self.config.greeting == "" else None
