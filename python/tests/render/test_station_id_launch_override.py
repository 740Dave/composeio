"""
[DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE] compliance tests.

A console's duty-station id is a run-time-populated binding attribute
(document `variables:` + a LAUNCH-source override), not a scene constant.
The seam is the EXISTING [CXC-BIND-VARS] `$name` substitution — this suite
guards:

  * NO override -> a scene renders EXACTLY as before this existed
    (`compose_projects/carousel/bsg_app.compose.yaml` still boots
    cic.1.helm / cic.2.helm).
  * A launch override (env `COMPOSEIO_VAR_<NAME>` and/or `--var name=value`)
    replaces the declared default, and every namespace derived from
    `station` (console/subtab/status/power/radio) follows it.
  * Two independently-launched instances given the SAME override id MIRROR
    (identical namespaces); given DIFFERENT overrides they stay independent.
  * An override for a variable name the scene does NOT declare is a no-op
    (never silently promoted to a new global).

NOTE: deliberately does NOT import `ColonialConsoleWidget` and assert
`isinstance` against a module-level reference — `test_colonial_radio_buttons
.py::test_promoted_widgets_have_no_compose_projects_dependency` purges every
`compose_projects.*` entry from `sys.modules` as ITS OWN test setup (proving
the promoted core widgets carry no BSG dependency), which — for any test file
that sorts after it and holds a stale collection-time class reference —
makes a LATER re-import mint a distinct class object and breaks `isinstance`.
That is a pre-existing test-order hazard in the suite, not a defect in the
station-id runtime-override mechanism this file tests; checking
`widget_type`/attributes instead of class identity sidesteps it entirely,
which is also the right assertion for THIS suite regardless (it guards
behavior, not the widget's Python type).

Offscreen (windowed=False); no display, no broker required.
"""

from __future__ import annotations

import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# python/ -> repo root -> compose_projects/carousel/bsg_app.compose.yaml
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
# [CXC-PROJECT-WIDGETS] the colonial_console widget is project-local to BSG;
# the repo root must be importable to reach compose_projects.bsg.widgets.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SCENE = os.path.join(REPO_ROOT, "compose_projects", "carousel",
                     "bsg_app.compose.yaml")

from composeio.render.engine.runtime import (
    RenderApp, _parse_var_flag, _variable_overrides_from_env,
    VARIABLE_OVERRIDE_ENV_PREFIX)
from composeio.render.engine.bindings import apply_variable_launch_overrides


def _app(variable_overrides=None):
    app = RenderApp.from_file(SCENE, windowed=False,
                              variable_overrides=variable_overrides)
    app.start()
    app.drain_updates()
    app.render_frame()
    return app


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def _consoles(app):
    by = _by_id(app)
    return by["cic_top"], by["cic_bottom"]


# ---------------------------------------------------------------------------
# Backward compatibility: no override
# ---------------------------------------------------------------------------

def test_no_override_matches_todays_defaults():
    """No override -> the scene resolves EXACTLY as before variables: existed:
    top=cic.1.helm, bottom=cic.2.helm, and every derived namespace follows."""
    app = _app()
    try:
        top, bot = _consoles(app)
        assert top.widget_type == "colonial_console"
        assert bot.widget_type == "colonial_console"
        assert top.station == "cic.1.helm"
        assert bot.station == "cic.2.helm"
        assert top.console_namespace == "colonial_console/cic.1.helm/console"
        assert bot.console_namespace == "colonial_console/cic.2.helm/console"
        assert top.status_namespace == "colonial_console/cic.1.helm/status"
        assert bot.status_namespace == "colonial_console/cic.2.helm/status"
        # the resolved document variables are retained on the Composition
        # ([CXC-HMON-STATE]) — confirm they carry the (unoverridden) defaults.
        assert app.comp.variables["station_1"] == "cic.1.helm"
        assert app.comp.variables["station_2"] == "cic.2.helm"
    finally:
        app.stop()


def test_no_override_empty_dict_also_matches_defaults():
    """An explicit-but-empty override map behaves identically to None."""
    app = _app(variable_overrides={})
    try:
        top, bot = _consoles(app)
        assert top.station == "cic.1.helm"
        assert bot.station == "cic.2.helm"
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Launch override changes the station
# ---------------------------------------------------------------------------

def test_launch_override_changes_only_the_overridden_station():
    app = _app(variable_overrides={"station_1": "cic.9.mirror"})
    try:
        top, bot = _consoles(app)
        assert top.station == "cic.9.mirror"
        assert top.console_namespace == "colonial_console/cic.9.mirror/console"
        assert top.status_namespace == "colonial_console/cic.9.mirror/status"
        # bottom is UNAFFECTED — this override named only station_1.
        assert bot.station == "cic.2.helm"
        assert bot.console_namespace == "colonial_console/cic.2.helm/console"
    finally:
        app.stop()


def test_launch_override_both_stations_independently():
    app = _app(variable_overrides={"station_1": "cic.9.a",
                                   "station_2": "cic.9.b"})
    try:
        top, bot = _consoles(app)
        assert top.station == "cic.9.a"
        assert bot.station == "cic.9.b"
        assert top.station != bot.station
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Mirror vs. independent — David's driving use case
# ---------------------------------------------------------------------------

def test_two_instances_same_override_mirror_the_same_namespace():
    """Two SEPARATE launches (two physical monitors) given the SAME override
    id resolve to the SAME namespace — the mirror case ([CXC-CIC-DUTY-STATION]
    scoping already keys purely off this id, so identical ids ARE the mirror)."""
    app_a = _app(variable_overrides={"station_1": "cic.5.mirror"})
    app_b = _app(variable_overrides={"station_1": "cic.5.mirror"})
    try:
        top_a, _ = _consoles(app_a)
        top_b, _ = _consoles(app_b)
        assert top_a.station == top_b.station == "cic.5.mirror"
        assert top_a.console_namespace == top_b.console_namespace
    finally:
        app_a.stop()
        app_b.stop()


def test_two_instances_different_overrides_stay_independent():
    app_a = _app(variable_overrides={"station_1": "cic.5.a"})
    app_b = _app(variable_overrides={"station_1": "cic.5.b"})
    try:
        top_a, _ = _consoles(app_a)
        top_b, _ = _consoles(app_b)
        assert top_a.station == "cic.5.a"
        assert top_b.station == "cic.5.b"
        assert top_a.console_namespace != top_b.console_namespace
    finally:
        app_a.stop()
        app_b.stop()


# ---------------------------------------------------------------------------
# Undeclared-name override: no-op, never a silent new global
# ---------------------------------------------------------------------------

def test_override_for_undeclared_name_is_a_noop_and_logged(caplog):
    with caplog.at_level(logging.WARNING):
        app = _app(variable_overrides={"station_1": "cic.9.mirror",
                                       "not_a_declared_variable": "x"})
    try:
        top, bot = _consoles(app)
        assert top.station == "cic.9.mirror"      # the declared one still applies
        assert bot.station == "cic.2.helm"
        assert any("not_a_declared_variable" in rec.message
                   for rec in caplog.records)
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# The binding-level unit: overrides participate in $name chaining exactly
# like the default they replace ([CXC-BIND-VARS])
# ---------------------------------------------------------------------------

def test_apply_variable_launch_overrides_merges_declared_names_only(caplog):
    declared = {"station_1": "cic.1.helm", "station_2": "cic.2.helm"}
    with caplog.at_level(logging.WARNING):
        merged = apply_variable_launch_overrides(
            declared, {"station_1": "cic.7.x", "ghost": "y"})
    assert merged == {"station_1": "cic.7.x", "station_2": "cic.2.helm"}
    assert any("ghost" in rec.message for rec in caplog.records)


def test_apply_variable_launch_overrides_none_is_passthrough():
    declared = {"station_1": "cic.1.helm"}
    assert apply_variable_launch_overrides(declared, None) is declared
    assert apply_variable_launch_overrides(declared, {}) is declared


# ---------------------------------------------------------------------------
# The launch sources: env-var prefix + `--var` CLI flag
# ---------------------------------------------------------------------------

def test_variable_overrides_from_env_reads_prefixed_lowercased():
    env = {
        f"{VARIABLE_OVERRIDE_ENV_PREFIX}STATION_1": "cic.3.env",
        f"{VARIABLE_OVERRIDE_ENV_PREFIX}station_2": "cic.4.env",
        "UNRELATED": "ignored",
    }
    out = _variable_overrides_from_env(env)
    assert out == {"station_1": "cic.3.env", "station_2": "cic.4.env"}


def test_variable_overrides_from_env_empty_when_absent():
    assert _variable_overrides_from_env({"UNRELATED": "x"}) == {}


def test_parse_var_flag_splits_on_first_equals():
    assert _parse_var_flag("station_1=cic.2.helm") == ("station_1", "cic.2.helm")
    # a value that itself contains '=' stays intact past the first split
    assert _parse_var_flag("k=a=b") == ("k", "a=b")


def test_parse_var_flag_rejects_missing_equals():
    import pytest
    with pytest.raises(ValueError):
        _parse_var_flag("no_equals_here")
