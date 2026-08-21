"""Unit tests for the Home Assistant action bridge
(``composeio.integrations.homeassistant``).

Every network call is exercised against a MOCKED transport — no live HA.
Asserts the exact REST request (URL, domain-from-entity, bearer header,
body) and that the token never leaks into logs / exception text.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.integrations import homeassistant as ha
from composeio.integrations.homeassistant import HAError

_TOKEN = "super-secret-token-DO-NOT-LOG"
_URL = "http://ha.local:8123"


class _MockTransport:
    """Records the last request; returns a canned (status, text)."""

    def __init__(self, status=200, text="[]"):
        self.status = status
        self.text = text
        self.calls = []

    def __call__(self, method, url, *, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": body, "timeout": timeout})
        return self.status, self.text

    @property
    def last(self):
        return self.calls[-1]


# ---------------------------------------------------------------- request shape
def test_ha_call_builds_correct_service_request():
    tx = _MockTransport()
    ha.ha_call("switch.red_alert_lights", "toggle",
               base_url=_URL, token=_TOKEN, transport=tx)
    c = tx.last
    assert c["method"] == "POST"
    assert c["url"] == "http://ha.local:8123/api/services/switch/toggle"
    assert c["headers"]["Authorization"] == f"Bearer {_TOKEN}"
    assert c["headers"]["Content-Type"] == "application/json"
    assert c["body"] == {"entity_id": "switch.red_alert_lights"}


def test_domain_inferred_from_entity_id():
    tx = _MockTransport()
    ha.turn_on("light.living_room", base_url=_URL, token=_TOKEN, transport=tx)
    assert tx.last["url"].endswith("/api/services/light/turn_on")
    ha.turn_off("switch.foo", base_url=_URL, token=_TOKEN, transport=tx)
    assert tx.last["url"].endswith("/api/services/switch/turn_off")


def test_base_url_trailing_slash_normalized():
    tx = _MockTransport()
    ha.toggle("switch.foo", base_url=_URL + "/", token=_TOKEN, transport=tx)
    c_url = tx.last["url"]
    assert "//api" not in c_url.replace("http://", "")


def test_get_state_reads_states_endpoint():
    tx = _MockTransport(text='{"entity_id": "switch.foo", "state": "on"}')
    st = ha.get_state("switch.foo", base_url=_URL, token=_TOKEN, transport=tx)
    assert tx.last["method"] == "GET"
    assert tx.last["url"].endswith("/api/states/switch.foo")
    assert tx.last["body"] is None
    assert st["state"] == "on"


def test_invalid_entity_id_rejected():
    tx = _MockTransport()
    with pytest.raises(HAError):
        ha.ha_call("no_domain_here", "toggle", base_url=_URL, token=_TOKEN,
                   transport=tx)


# ---------------------------------------------------------------- credentials
def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("HA_URL", _URL)
    monkeypatch.setenv("HA_TOKEN", _TOKEN)
    base, tok = ha.resolve_credentials()
    assert base == _URL and tok == _TOKEN


def test_missing_credentials_raise_without_token(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    with pytest.raises(HAError) as ei:
        ha.resolve_credentials()
    assert "base_url" in str(ei.value)


# ---------------------------------------------------------------- token hygiene
def test_token_never_in_exception_on_transport_failure():
    def boom(method, url, *, headers, body, timeout):
        # A lower layer that echoes the auth header into its error message.
        raise RuntimeError(f"connection refused (headers={headers})")

    with pytest.raises(HAError) as ei:
        ha.ha_call("switch.foo", "toggle", base_url=_URL, token=_TOKEN,
                   transport=boom)
    assert _TOKEN not in str(ei.value)
    # and the chained cause is dropped so it cannot leak via __cause__
    assert ei.value.__cause__ is None


def test_token_never_in_exception_on_http_error():
    tx = _MockTransport(status=401, text="unauthorized")
    with pytest.raises(HAError) as ei:
        ha.toggle("switch.foo", base_url=_URL, token=_TOKEN, transport=tx)
    assert _TOKEN not in str(ei.value)
    assert "401" in str(ei.value)


# ---------------------------------------------------------------- runner tools
def _collect(tool_fn, args):
    events = []
    tool_fn(args, events.append)
    return events


def test_runner_toggle_emits_ok_result():
    tx = _MockTransport()
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN, transport=tx)
    events = _collect(tools["homeassistant.toggle"],
                      {"entity": "switch.red_alert_lights"})
    results = [e for e in events if e["kind"] == "result"]
    assert results and results[-1]["ok"] is True
    assert tx.last["url"].endswith("/api/services/switch/toggle")


def test_runner_accepts_target_alias():
    tx = _MockTransport()
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN, transport=tx)
    _collect(tools["homeassistant.turn_on"], {"target": "switch.foo"})
    assert tx.last["url"].endswith("/api/services/switch/turn_on")


def test_runner_status_returns_state():
    tx = _MockTransport(text='{"state": "off"}')
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN, transport=tx)
    events = _collect(tools["homeassistant.status"], {"entity": "switch.foo"})
    assert events[-1]["ok"] is True and events[-1]["state"] == "off"


def test_runner_missing_entity_fails_cleanly():
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN,
                               transport=_MockTransport())
    events = _collect(tools["homeassistant.toggle"], {})
    assert events[-1]["ok"] is False


def test_make_invoke_refuses_unregistered_tool():
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN,
                               transport=_MockTransport())
    invoke = ha.make_invoke(tools, threaded=False)
    events = []
    invoke("fleetpower.nuke_everything", {}, on_event=events.append)
    assert events[-1]["ok"] is False and "REFUSED" in events[-1]["summary"]


def test_make_invoke_runner_failure_never_logs_token():
    tx = _MockTransport(status=500, text="boom")
    tools = ha.ha_runner_tools(base_url=_URL, token=_TOKEN, transport=tx)
    invoke = ha.make_invoke(tools, threaded=False)
    events = []
    invoke("homeassistant.toggle", {"entity": "switch.foo"},
           on_event=events.append)
    assert events[-1]["ok"] is False
    assert _TOKEN not in events[-1]["summary"]
