"""Fully-software self-test for the WLED DRIVEN adaptor -- NO real WLED needed.

Verifies (per the workstream acceptance):
  * mapping: light/brightness=0.5 -> bri 128; light/on=False -> {"on": False};
    light/color="#ff0000" -> seg[0].col == [[255,0,0]]; scene/mood="storm" ->
    the mapped preset ({"ps": 5}); scene/mood="calm" -> mapped effect.
  * HTTP path: a namespace change over a real ComposeIOBus produces the correct
    POST to http://<host>/json/state with the correct JSON body (urlopen patched).
  * MQTT path: publish goes to `<deviceTopic>/api` with the JSON state payload
    (paho client injected/patched).

Machine-green via mock. A real WLED device is glass-pending (standard API).

Run:  python -m composeio.adaptors.wled --selftest
"""

from __future__ import annotations

import json
from typing import Any

from composeio import ComposeIOBus
from composeio.adaptors.wled import (
    WLEDAdaptor,
    WLEDMapping,
    WLEDHTTPSender,
    WLEDMQTTSender,
)
from composeio.adaptors.wled import senders as senders_mod


class _StubSender:
    """Captures every WLED state fragment the adaptor emits."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, state: dict) -> Any:
        self.sent.append(state)
        return state

    def close(self) -> None:
        pass


class _FakeMQTTClient:
    """Stand-in paho client: records (topic, payload) publishes."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0):
        self.published.append((topic, payload))
        return ("MID", 0)


def _check(label: str, cond: bool, detail: str = "") -> tuple[str, bool, str]:
    return (label, bool(cond), detail)


def run() -> bool:
    results: list[tuple[str, bool, str]] = []

    # --- 1. pure mapping ---------------------------------------------------
    m = WLEDMapping()
    frag_bri = m.render("light/brightness", 0.5)
    results.append(_check("brightness 0.5 -> bri 128",
                          frag_bri == {"bri": 128}, str(frag_bri)))

    frag_bri_raw = m.render("light/brightness", 200)
    results.append(_check("brightness 200 (raw) -> bri 200",
                          frag_bri_raw == {"bri": 200}, str(frag_bri_raw)))

    frag_off = m.render("light/on", False)
    results.append(_check("light/on False -> {'on': False}",
                          frag_off == {"on": False}, str(frag_off)))

    frag_on = m.render("light/on", "on")
    results.append(_check("light/on 'on' -> {'on': True}",
                          frag_on == {"on": True}, str(frag_on)))

    frag_col = m.render("light/color", "#ff0000")
    ok_col = (frag_col.get("seg", [{}])[0].get("col") == [[255, 0, 0]])
    results.append(_check("light/color '#ff0000' -> seg[0].col [[255,0,0]]",
                          ok_col, str(frag_col)))

    frag_col_named = m.render("light/color", "green")
    ok_named = frag_col_named.get("seg", [{}])[0].get("col") == [[0, 255, 0]]
    results.append(_check("light/color 'green' -> seg[0].col [[0,255,0]]",
                          ok_named, str(frag_col_named)))

    frag_storm = m.render("scene/mood", "storm")
    results.append(_check("scene/mood 'storm' -> preset {'ps': 5}",
                          frag_storm == {"ps": 5}, str(frag_storm)))

    frag_calm = m.render("scene/mood", "calm")
    ok_calm = frag_calm.get("seg", [{}])[0].get("fx") == 2 and frag_calm.get("bri") == 90
    results.append(_check("scene/mood 'calm' -> mapped effect (fx=2, bri=90)",
                          ok_calm, str(frag_calm)))

    # overridable mapping (per-install)
    m2 = WLEDMapping().override("scene/mood",
                                {"kind": "scene", "table": {"storm": {"ps": 9}}})
    results.append(_check("mapping override: storm -> {'ps': 9}",
                          m2.render("scene/mood", "storm") == {"ps": 9},
                          str(m2.render("scene/mood", "storm"))))

    # --- 2. end-to-end over a real ComposeIOBus (stub sender) --------------
    bus = ComposeIOBus()
    for ns in ("light/on", "light/brightness", "light/color", "scene/mood"):
        bus.register_namespace(ns, engine_config="json")
    bus.start()
    stub = _StubSender()
    WLEDAdaptor(bus, stub, mapping=WLEDMapping()).attach()

    bus.publish_state("light/brightness", 0.5)
    bus.publish_state("light/on", False)
    bus.publish_state("light/color", "#ff0000")
    bus.publish_state("scene/mood", "storm")
    bus.stop()

    results.append(_check("bus path: brightness change -> {'bri':128} sent",
                          {"bri": 128} in stub.sent, str(stub.sent)))
    results.append(_check("bus path: on=False -> {'on':False} sent",
                          {"on": False} in stub.sent, str(stub.sent)))
    results.append(_check("bus path: color -> seg col sent",
                          any(s.get("seg", [{}])[0].get("col") == [[255, 0, 0]]
                              for s in stub.sent), str(stub.sent)))
    results.append(_check("bus path: storm -> {'ps':5} sent",
                          {"ps": 5} in stub.sent, str(stub.sent)))

    # --- 3. HTTP path: patch urlopen, assert URL + JSON body ---------------
    captured: dict[str, Any] = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"success":true}'

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["ctype"] = req.headers.get("Content-type")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    orig_urlopen = senders_mod.urlopen
    senders_mod.urlopen = _fake_urlopen
    try:
        http = WLEDHTTPSender("192.0.2.60")
        http.send({"bri": 128})
    finally:
        senders_mod.urlopen = orig_urlopen

    results.append(_check("http: POST url == http://192.0.2.60/json/state",
                          captured.get("url") == "http://192.0.2.60/json/state",
                          str(captured.get("url"))))
    results.append(_check("http: method POST + JSON content-type",
                          captured.get("method") == "POST"
                          and captured.get("ctype") == "application/json",
                          f"{captured.get('method')} {captured.get('ctype')}"))
    results.append(_check("http: body == {'bri':128}",
                          captured.get("body") == {"bri": 128},
                          str(captured.get("body"))))

    # --- 4. MQTT path: injected fake client, assert topic + payload --------
    fake = _FakeMQTTClient()
    mq = WLEDMQTTSender(device_topic="wled/livingroom", client=fake)
    mq.send({"on": True, "bri": 200})
    mq.send_color("#ff0000")

    ok_api = (len(fake.published) >= 1
              and fake.published[0][0] == "wled/livingroom/api"
              and json.loads(fake.published[0][1]) == {"on": True, "bri": 200})
    results.append(_check("mqtt: state -> topic 'wled/livingroom/api' + JSON payload",
                          ok_api, str(fake.published[:1])))
    ok_col_topic = (len(fake.published) >= 2
                    and fake.published[1] == ("wled/livingroom/col", "#ff0000"))
    results.append(_check("mqtt: color -> topic 'wled/livingroom/col' payload '#ff0000'",
                          ok_col_topic, str(fake.published[1:2])))

    # --- report ------------------------------------------------------------
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("WLED DRIVEN adaptor self-test")
    print("=" * 60)
    for label, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {label}"
        if not ok and detail:
            line += f"   <- got: {detail}"
        print(line)
    print("=" * 60)
    print(f"{passed}/{total} checks passed")
    return passed == total


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
