"""Spec 95 r24, [CXC-PC-CONFORMANCE-SCRIPT] ``host-section-hook`` and
``foreign-host``, and NC4's generalised positive control
([CXC-PC-NC-UNKNOWN-CORE-KEY], [CXC-PC-UNKNOWN-KEYS] verify-machine).

The stub hosts here are STRICT about their own section, as Gnomon's R-SECTION
validator is (the finding that produced r24): a host that only accepts
``{"probe": 1}`` cannot pass unless the runner wrote that value into EVERY
manifest it handed over, and a host that refuses any unknown key in its own
section cannot pass NC4 if the runner put the unknown key there. Each rule has
a paired negative control in this file that removes the rule and expects RED.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from composebus.bus import ComposeIOBus

from compose_plugins import conformance
from compose_plugins.errors import Refusal
from compose_plugins.host import ConsentDecision, Host
from compose_plugins.lifecycle import PluginManager

PROBE = {"probe": 1}


class StrictHost(Host):
    """Accepts its OWN section only when it equals ``expected`` exactly."""

    def __init__(self, name, root, level, expected):
        super().__init__(name, state_root=root, minimum_isolation=level)
        self._bus = ComposeIOBus(bus_id=f"stub-{name}")
        self.expected = expected

    def consent_gate(self, r):
        return ConsentDecision("granted" if r.accepted and r.operator else "refused",
                               r.operator or "stub:no-operator")

    def validate_section(self, s):
        if s != self.expected:
            return [Refusal("", f"{s!r}: this strict host accepts only {self.expected!r}")]
        return []

    def render_section(self, s):
        return []

    def bus(self):
        return self._bus


class RecordingManager(PluginManager):
    """Records, for every manifest handed to the host (prepare_install /
    prepare_upgrade), the host's own section, the foreign host's section and
    whether the prompt's digest is the digest of the bytes on disk."""

    def __init__(self, host, log, foreign):
        super().__init__(host)
        self.log, self.foreign = log, foreign

    def _note(self, source, fn):
        raw = (Path(source) / "manifest.json").read_bytes()
        hosts = json.loads(raw)["hosts"]
        entry = {"own": hosts.get(self.host.name, "<ABSENT>"),
                 "foreign": hosts.get(self.foreign, "<ABSENT>"),
                 "sha_on_disk": hashlib.sha256(raw).hexdigest(), "prompt_sha": None}
        self.log.append(entry)
        p = fn(source)
        entry["prompt_sha"] = p.manifest_sha256
        return p

    def prepare_install(self, source, *a, **k):
        return self._note(source, lambda s: super(RecordingManager, self).prepare_install(s, *a, **k))

    def prepare_upgrade(self, source, *a, **k):
        return self._note(source, lambda s: super(RecordingManager, self).prepare_upgrade(s, *a, **k))


def make_target(*, name="composeio", foreign="gnomon", section=None, expected=None,
                log=None, calls=None, omit_foreign=False):
    """A target factory. ``section`` set -> the target provides host_section
    returning it (and records each fixture id in ``calls``)."""
    log = [] if log is None else log
    expected = ({} if section is None else section) if expected is None else expected

    class T:
        def __init__(self, isolation, workdir):
            self.host = StrictHost(name, workdir, isolation, expected)
            self.manager = RecordingManager(self.host, log, foreign)
            self.bus = self.host.bus()
            self.isolation = isolation
            self.operator = "stub-operator"

        def close(self):
            self.manager.close()

    if not omit_foreign:
        T.foreign_host = foreign
    if section is not None:
        def host_section(self, fixture):
            if calls is not None:
                calls.append(fixture)
            return dict(section)
        T.host_section = host_section
    return T


def _run(factory, tmp_path, level="inprocess"):
    out = io.StringIO()
    rc = conformance.run(factory, level, out=out, workdir=tmp_path)
    return rc, out.getvalue()


def _step(text, sid):
    return text.split(f"STEP {sid} ")[1].split("\nSTEP ")[0]


# --------------------------------------------------------------------------- #
# host-section-hook: the provided value lands in EVERY manifest, sha covers it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_host_section_probe_is_written_into_every_installed_manifest(tmp_path, level):
    log, calls = [], []
    rc, text = _run(make_target(section=PROBE, log=log, calls=calls), tmp_path, level)
    assert rc == 0, text
    assert log, "no manifest was handed to the host: the instrument saw nothing"
    assert all(e["own"] == PROBE for e in log), [e["own"] for e in log]
    # the consented digest is the digest of the manifest WITH the section written
    # (every prompt that was issued; refused prepares issue none)
    prompted = [e for e in log if e["prompt_sha"] is not None]
    assert prompted and all(e["prompt_sha"] == e["sha_on_disk"] for e in prompted)
    # called with the script's own fixture ids, and with every one of them
    assert set(calls) <= set(conformance.FIXTURE_IDS)
    assert set(calls) == set(conformance.FIXTURE_IDS), sorted(set(calls))


def test_host_section_negative_control_without_the_write_the_strict_host_refuses(
        tmp_path, monkeypatch):
    """Remove the rule (the runner ignores the hook and writes {}): the strict
    probe host must then refuse, and the run must go RED."""
    monkeypatch.setattr(conformance, "host_section", lambda t, fixture: {})
    rc, text = _run(make_target(section=PROBE), tmp_path)
    assert rc == 1
    assert "-> FAIL" in _step(text, "1")


def test_without_the_hook_every_manifest_carries_empty_section(tmp_path):
    log = []
    rc, text = _run(make_target(section=None, expected={}, log=log), tmp_path)
    assert rc == 0, text
    assert log and all(e["own"] == {} for e in log)
    assert "hosts.composeio written for fixture hello-world-v1: {}" in text


def test_without_the_hook_negative_control_a_non_empty_default_goes_red(tmp_path, monkeypatch):
    """Remove the ``{}`` default (write something else): the host that accepts
    only ``{}`` must refuse."""
    monkeypatch.setattr(conformance, "host_section",
                        lambda t, fixture: {"not": "empty"})
    rc, text = _run(make_target(section=None, expected={}), tmp_path)
    assert rc == 1 and "-> FAIL" in _step(text, "1")


def test_host_section_rejects_an_id_outside_the_script_vocabulary():
    with pytest.raises(ValueError, match="unknown conformance fixture id"):
        conformance.host_section(object(), "hello-world-v3")


# --------------------------------------------------------------------------- #
# NC4's positive control writes at hosts.<foreign_host>
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("own, foreign", [("composeio", "gnomon"), ("gnomon", "composeio")])
def test_nc4_positive_control_uses_foreign_host_on_both_hosts(tmp_path, own, foreign):
    """A strict host (own section must be exactly {}) passes NC4 named either
    way round: the unknown key is never placed in its own section."""
    log = []
    rc, text = _run(make_target(name=own, foreign=foreign, expected={}, log=log), tmp_path)
    assert rc == 0, text
    nc4 = _step(text, "NC4")
    assert "-> PASS" in nc4
    assert f"unknown key in hosts.{foreign} -> state: installed" in nc4
    assert any(isinstance(e["foreign"], dict) and "permisions" in e["foreign"] for e in log)
    assert all("permisions" not in e["own"] for e in log if isinstance(e["own"], dict))


def test_nc4_negative_control_literal_hosts_gnomon_fails_on_the_gnomon_host(
        tmp_path, monkeypatch):
    """Remove the rule (NC4 writes at a literal hosts.gnomon, the r23 runner):
    on a strict host NAMED gnomon, NC4 must go RED."""
    real = conformance.Run.fixture

    def literal_gnomon(self, t, version, fixture, edit=None):
        if fixture == "NC4" and edit is not None:
            def edit_(d):
                d["hosts"]["gnomon"].update(permisions="gnomon's")
            return real(self, t, version, fixture, edit_)
        return real(self, t, version, fixture, edit)
    monkeypatch.setattr(conformance.Run, "fixture", literal_gnomon)
    rc, text = _run(make_target(name="gnomon", foreign="composeio", expected={}), tmp_path)
    assert rc == 1
    assert "-> FAIL" in _step(text, "NC4")


# --------------------------------------------------------------------------- #
# foreign-host is REQUIRED and validated before step 1
# --------------------------------------------------------------------------- #
def test_missing_foreign_host_refuses_before_step_1(tmp_path):
    log = []
    rc, text = _run(make_target(omit_foreign=True, log=log), tmp_path)
    assert rc == 2
    assert "foreign_host missing" in text
    assert "STEP " not in text and log == []
    assert not list(tmp_path.glob("pkg-*"))


@pytest.mark.parametrize("bad", ["composeio", "Bad-Name", "bus", "code", "permission", ""])
def test_invalid_or_self_equal_foreign_host_refuses_naming_the_value(tmp_path, bad):
    log = []
    rc, text = _run(make_target(foreign=bad, log=log), tmp_path)
    assert rc == 2
    assert "REFUSED before step 1" in text and repr(bad) in text
    assert "STEP " not in text and log == []
    assert not list(tmp_path.glob("pkg-*"))


def test_foreign_host_negative_control_without_the_check_the_run_proceeds(tmp_path, monkeypatch):
    """Remove the rule (no validation): a missing foreign_host is no longer
    refused before step 1; the runner reaches the steps (and is not rc 2)."""
    monkeypatch.setattr(conformance, "check_foreign_host",
                        lambda t: getattr(t, "foreign_host", "gnomon"))
    rc, text = _run(make_target(omit_foreign=True), tmp_path)
    assert rc != 2 and "STEP 1 " in text and "foreign_host missing" not in text


def test_foreign_host_negative_control_self_equal_accepted_without_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(conformance, "check_foreign_host", lambda t: t.foreign_host)
    rc, text = _run(make_target(foreign="composeio"), tmp_path)
    assert rc != 2 and "STEP 1 " in text
