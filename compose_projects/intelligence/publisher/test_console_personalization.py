#!/usr/bin/env python3
"""test_console_personalization — [SPEC:CXC-CONSOLE-PROD] [CXC-ICP-FOUND-A].

Foundation A: the persisted console-personalization model, folded into a
``console`` block SIBLING to ``lifecycle`` in the same meta-object, written by
the config_persister atomic read-modify-write it already uses (reuse-by-
reference, [CXC-ICP-REUSE]).  This is the pytest face of the daemon's
``--selftest`` group (g); it asserts the five fields fold/no-op/round-trip and
that an empty meta-object yields the stated defaults.

Run: python3 -m pytest compose_projects/intelligence/publisher/test_console_personalization.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # compose_projects/intelligence/publisher
INTEL = os.path.dirname(HERE)                               # intelligence
REPO = os.path.dirname(os.path.dirname(INTEL))                      # repo root (compose_projects is in between)
for _d in (HERE, os.path.join(REPO, "python")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from config_persister import (                              # noqa: E402
    DEFAULT_CONSOLE, ConfigPersister, _console_ns, _CONSOLE_FIELDS,
    console_settings, persist_config, seed_values)

CID = "architect"
FONT = _console_ns(CID, "font_scale")
WIN = _console_ns(CID, "window")
THEME = _console_ns(CID, "theme")
TABS = _console_ns(CID, "tabs")
DASH = _console_ns(CID, "dashboard")


# ── defaults ────────────────────────────────────────────────────────────────
def test_empty_meta_yields_stated_defaults():
    assert console_settings({}) == DEFAULT_CONSOLE
    assert DEFAULT_CONSOLE == {
        "window": {"w": 1280, "h": 1740},
        "font_scale": 1.0,
        "theme": "dark",
        # [CXC-ICP-S3-THEME] David's D2 addition: per-instance custom colors
        # (bg/accent) for the dark theme — empty {} = no override.
        "theme_custom": {},
        # [CXC-ICP-S6-SCROLLDIR] per-instance scroll-direction preference;
        # "natural" = respect the OS-provided wheel delta.
        "scroll_direction": "natural",
        "tabs": {},
        "dashboard": {"placements": []},
    }
    assert seed_values({})["console"] == DEFAULT_CONSOLE


# ── fold + sibling preservation ─────────────────────────────────────────────
def test_field_folds_into_console_block_preserving_lifecycle():
    meta = {"id": CID, "lifecycle": {"retention_days": 30, "note": "keep"}}
    meta, changed = persist_config(meta, FONT, 1.25, CID)
    assert changed and meta["console"]["font_scale"] == 1.25
    assert meta["lifecycle"] == {"retention_days": 30, "note": "keep"}


def test_sibling_console_keys_not_clobbered():
    meta = {"id": CID}
    meta, _ = persist_config(meta, FONT, 1.5, CID)
    meta, _ = persist_config(meta, THEME, "high-contrast", CID)
    meta, _ = persist_config(meta, WIN, {"w": 1024, "h": 768}, CID)
    assert meta["console"]["font_scale"] == 1.5
    assert meta["console"]["theme"] == "high-contrast"
    assert meta["console"]["window"] == {"w": 1024, "h": 768}


def test_tabs_and_dashboard_land_as_objects():
    meta = {"id": CID}
    meta, _ = persist_config(
        meta, TABS, {"collaboration": {"widgets": ["a", "b"], "hidden": ["c"]}}, CID)
    meta, _ = persist_config(
        meta, DASH, {"placements": [{"widget": "gauge", "row": 0, "col": 0,
                                     "w": 2, "h": 1}]}, CID)
    assert meta["console"]["tabs"]["collaboration"]["widgets"] == ["a", "b"]
    assert meta["console"]["dashboard"]["placements"][0]["widget"] == "gauge"


# ── no-ops ──────────────────────────────────────────────────────────────────
def test_identical_edit_is_a_noop():
    meta = {"id": CID}
    meta, first = persist_config(meta, FONT, 1.25, CID)
    _, second = persist_config(meta, FONT, 1.25, CID)
    assert first is True and second is False


def test_invalid_value_is_a_noop_and_creates_no_block():
    meta = {"id": CID}
    m1, c1 = persist_config(dict(meta), THEME, "", CID)        # empty theme
    m2, c2 = persist_config(dict(meta), FONT, -1, CID)          # non-positive
    m3, c3 = persist_config(dict(meta), WIN, {"w": 0, "h": 5}, CID)  # degenerate
    assert c1 is False and c2 is False and c3 is False
    assert "console" not in m1 and "console" not in m2 and "console" not in m3


def test_unknown_console_field_is_a_noop():
    meta = {"id": CID}
    _, changed = persist_config(meta, _console_ns(CID, "not_a_field"), 1, CID)
    assert changed is False


# ── round-trip ──────────────────────────────────────────────────────────────
def test_seed_values_round_trips_console_block():
    meta = {"id": CID}
    meta, _ = persist_config(meta, FONT, 1.25, CID)
    meta, _ = persist_config(meta, THEME, "high-contrast", CID)
    meta, _ = persist_config(meta, WIN, {"w": 1024, "h": 768}, CID)
    console = seed_values(meta)["console"]
    assert console["font_scale"] == 1.25
    assert console["theme"] == "high-contrast"
    assert console["window"] == {"w": 1024, "h": 768}
    # unpersisted fields fall back to defaults.
    assert console["tabs"] == {}
    assert console["dashboard"] == {"placements": []}


def test_full_restart_round_trip_through_persister(tmp_path):
    path = os.path.join(str(tmp_path), "architect.meta.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"id": CID, "role": "keep",
                   "lifecycle": {"compact_threshold": 0.93}}, fh)
    published = []
    p = ConfigPersister(path, lambda ns, val: published.append((ns, val)))
    assert p.on_config(FONT, 1.5, src="widget") is True
    # the persister ignores its OWN re-seed (loop-guard).
    assert p.on_config(FONT, 1.5, src="config_persister") is False
    with open(path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["console"]["font_scale"] == 1.5
    assert on_disk["role"] == "keep"                          # sibling survived
    assert on_disk["lifecycle"]["compact_threshold"] == 0.93  # lifecycle survived
    # SIMULATE A RESTART: a fresh persister re-seeds each console field.
    published.clear()
    ConfigPersister(path, lambda ns, val: published.append((ns, val))).reseed()
    pub = dict(published)
    for field in _CONSOLE_FIELDS:
        assert _console_ns(CID, field) in pub
    assert pub[FONT] == 1.5


# ── the spec's stated machine gate: the daemon --selftest exits 0 ────────────
def test_daemon_selftest_exits_zero():
    r = subprocess.run([sys.executable, os.path.join(HERE, "config_persister.py"),
                        "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # 7 -> 9 groups with [SPEC:CXC-SOURCES-ON-BUS] slice 5b: (h) the ECHO GUARD
    # that replaces the `src == config_persister` loop-guard the bus callback
    # contract removed, and (i) the ONE namespace list that drives declare +
    # subscribe + the SOB-C3 manifest.
    assert "9/9 groups" in r.stdout
