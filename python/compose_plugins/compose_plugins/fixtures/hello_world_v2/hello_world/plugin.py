"""hello-world v2 ([CXC-PC-HELLO-V2]).

ADDS config ``repeat: int = 1`` (pongs published PER PING, each with its own
incrementing ``n``) and ADDS subscribe EVENT ``plugins/hello_world/name``, which
only records the last value seen (it exists to widen permissions). With the
default ``repeat: 1`` behaviour equals v1.
"""
from typing import ClassVar

from pydantic import BaseModel

from composebus.plugin import ComposePlugin

GREETING = "plugins/hello_world/greeting"
PING = "plugins/hello_world/ping"
PONG = "plugins/hello_world/pong"
NAME = "plugins/hello_world/name"


class HelloWorld(ComposePlugin):
    class Config(BaseModel):
        greeting: str = "hello"
        repeat: int = 1

    name = "hello_world"
    version = "2.0.0"
    publishes = [GREETING, PONG]
    subscribes = [PING, NAME]
    bus_types: ClassVar[dict] = {GREETING: "STATE", PONG: "EVENT", PING: "EVENT",
                                 NAME: "EVENT"}

    def start(self, bus):
        self._bus = bus
        self._n = 0
        self._running = True
        self.last_name = None
        bus.publish_state(GREETING, self.config.greeting, source="plugin:hello_world")
        bus.subscribe_events(PING, self._on_ping)
        bus.subscribe_events(NAME, self._on_name)

    def _on_ping(self, namespace, event_type, data):
        if not getattr(self, "_running", False):
            return
        for _ in range(self.config.repeat):
            self._n += 1
            self._bus.publish_event(PONG, "pong",
                                    {"greeting": self.config.greeting, "n": self._n},
                                    source="plugin:hello_world")

    def _on_name(self, namespace, event_type, data):
        if getattr(self, "_running", False):
            self.last_name = data

    def stop(self):
        self._running = False
        self._bus = None

    def health_reason(self):
        return "empty greeting" if self.config.greeting == "" else None
