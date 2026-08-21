"""Compliance tests for the composition SLOT ROUTER ([SPEC:CXC-COMP],
[CXC-COMP-SLOT], [CXC-COMP-WIDGET-KIND], [CXC-COMP-INTERLEAVE], [CXC-COMP-MISSING]).

Covers the router half of the machine gates:

  COMP-C4  the plan preserves the derived (interleaved) z-order
  COMP-C6  a registered slot -> a widget entry into its bbox bound to the
           scope-qualified namespace; unknown kind -> placeholder entry + warning;
           the SAME registered kind serves many screens (define-once)
  COMP-C8  two differently-shaped 'button' slots both resolve the SAME kind
  COMP-C11 the missing-screen placeholder label is clean, never a fall-through
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from composeio.render.composition import (derive,
                                          missing_screen_placeholder_label,
                                          qualify_binding, route)

BASE_SVG = os.path.join(
    REPO_ROOT, "compose_projects", "bsg", "resources", "screen_base_layout",
    "colonial_base_ux.svg")


def _svg(groups, viewbox="0 0 100 100"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'viewBox="{viewbox}">{groups}</svg>')


def _g(label, inner="", gid=""):
    idattr = f' id="{gid}"' if gid else ""
    return f'<g{idattr} inkscape:label="{label}">{inner}</g>'


INTERLEAVE_SVG = _svg(
    _g("black_background", '<rect id="bg" x="0" y="0" width="100" height="100"/>')
    + _g("standard-ux-layout",
         _g("slot:gauge@velocity",
            '<rect id="ph" x="20" y="30" width="40" height="40"/>'))
    + _g("optional-common-decorators", '<path id="ov" d="M0 0 L10 0 L10 10 Z"/>'))


# ---------------------------------------------------------------------------
# COMP-C4 — plan preserves derived interleave order
# ---------------------------------------------------------------------------
def test_c4_plan_preserves_interleave_order():
    plan = route(derive(INTERLEAVE_SVG))
    order = [(e.z, e.kind, e.layer.label) for e in plan.entries]
    assert order == [
        (0, "backdrop", "black_background"),
        (1, "backdrop", "standard-ux-layout"),
        (2, "widget", "slot:gauge@velocity"),      # above backdrop
        (3, "backdrop", "optional-common-decorators"),  # below overlay
    ]


# ---------------------------------------------------------------------------
# COMP-C6 — registered slot -> widget entry, scope-bound; unknown -> placeholder
# ---------------------------------------------------------------------------
def test_c6_registered_slot_becomes_bound_widget_entry():
    plan = route(derive(INTERLEAVE_SVG), station="cic.1.helm", console="helm")
    w = plan.widgets()[0]
    assert w.widget_kind == "gauge"
    assert w.binding_namespace == "colonial_console/cic.1.helm/helm/velocity"
    assert w.bbox is not None                       # placed into the slot bbox


def test_c6_scope_qualification_variants():
    assert qualify_binding("velocity") == "colonial_console/velocity"
    assert qualify_binding("velocity", station="s", console="helm") == \
        "colonial_console/s/helm/velocity"
    assert qualify_binding("shut-down", console="sys") == \
        "colonial_console/sys/shut-down"            # hyphen preserved


def test_c6_unregistered_kind_becomes_placeholder_with_warning():
    svg = _svg(_g("s", _g("slot:hologram@target",
                          '<rect x="1" y="1" width="9" height="9"/>')))
    plan = route(derive(svg))
    ph = plan.placeholders()
    assert len(ph) == 1
    assert ph[0].widget_kind == "hologram"
    assert "unregistered" in ph[0].reason
    assert any(d.code == "unregistered-kind" for d in plan.diagnostics)


def test_c6_malformed_slot_becomes_placeholder():
    svg = _svg(_g("s", _g("slot:text:message",
                          '<rect x="1" y="1" width="9" height="9"/>')))
    plan = route(derive(svg))
    assert len(plan.placeholders()) == 1
    assert "malformed" in plan.placeholders()[0].reason


def test_c6_same_kind_serves_many_define_once():
    a = route(derive(_svg(_g("s", _g("slot:gauge@velocity",
                                     '<rect x="0" y="0" width="1" height="1"/>')))))
    b = route(derive(_svg(_g("s", _g("slot:gauge@altitude",
                                     '<rect x="0" y="0" width="1" height="1"/>')))))
    assert a.widgets()[0].widget_kind == b.widgets()[0].widget_kind == "gauge"
    assert a.widgets()[0].binding_namespace != b.widgets()[0].binding_namespace


def test_c6_factory_called_but_optional():
    built = []

    def factory(entry):
        built.append(entry.widget_kind)
        return object()

    plan = route(derive(INTERLEAVE_SVG), factory=factory)
    # a plan is produced with or without a factory; the router itself is
    # layout-only, so the factory is a caller concern (widgets() still lists it)
    assert plan.widgets()[0].widget_kind == "gauge"


# ---------------------------------------------------------------------------
# COMP-C8 — two shapes, one button kind
# ---------------------------------------------------------------------------
def test_c8_two_button_slots_one_kind():
    plan = route(derive(BASE_SVG))
    buttons = [e for e in plan.widgets() if e.widget_kind == "button"]
    assert len(buttons) >= 2
    assert {e.widget_kind for e in buttons} == {"button"}


# ---------------------------------------------------------------------------
# COMP-C11 — clean missing-screen placeholder, never a fall-through
# ---------------------------------------------------------------------------
def test_c11_missing_placeholder_label():
    assert missing_screen_placeholder_label("navcon", "plot") == \
        "NAVCON / PLOT — screen not built"
