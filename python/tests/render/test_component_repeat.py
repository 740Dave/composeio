"""
[SPEC:CXC-PRIM] [CXC-PRIM-REPEAT] — the `repeat` generator (load-time
component-expander unroll).

Unit-level tests over ``expand_components`` (a pure config->config
transform): a ``repeat`` child is unrolled into N concrete children with
``{{i}}`` = 0..N-1 and ``{{count}}`` = N stamped as typed literals, and it
composes with ``{{param}}`` / ``use:`` / nesting, applying layout keys
per-copy.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.components import expand_components


def _children(config):
    return config["widgets"][0]["children"]


# ---------------------------------------------------------------------------
# Core unroll: repeat N as: i -> N children, {{i}} = 0..N-1, {{count}} = N
# ---------------------------------------------------------------------------

def test_repeat_unrolls_to_n_children_with_index():
    config = {
        "widgets": [{
            "type": "stack", "id": "row", "direction": "row",
            "children": [
                {"repeat": 3, "as": "i",
                 "child": {"type": "rect", "id": "seg_{{i}}",
                           "tag": "{{i}} of {{count}}"}},
            ],
        }],
    }
    out = expand_components(config)
    kids = _children(out)
    assert len(kids) == 3
    assert [k["id"] for k in kids] == ["seg_0", "seg_1", "seg_2"]
    assert [k["tag"] for k in kids] == ["0 of 3", "1 of 3", "2 of 3"]
    # the repeat wrapper is gone; children are plain widget dicts
    assert all("repeat" not in k for k in kids)


def test_exact_index_token_is_a_typed_int():
    config = {
        "widgets": [{
            "type": "grid", "id": "g", "children": [
                {"repeat": 4, "as": "i",
                 "child": {"type": "rect", "id": "c{{i}}", "column": "{{i}}"}},
            ],
        }],
    }
    kids = _children(expand_components(config))
    cols = [k["column"] for k in kids]
    assert cols == [0, 1, 2, 3]
    assert all(isinstance(c, int) for c in cols)   # typed passthrough, not "0"


def test_default_index_name_is_i():
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"repeat": 2, "child": {"type": "rect", "id": "r{{i}}"}},
            ],
        }],
    }
    kids = _children(expand_components(config))
    assert [k["id"] for k in kids] == ["r0", "r1"]


def test_custom_index_name():
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"repeat": 2, "as": "k",
                 "child": {"type": "rect", "id": "r{{k}}",
                           "tag": "n={{count}}"}},
            ],
        }],
    }
    kids = _children(expand_components(config))
    assert [k["id"] for k in kids] == ["r0", "r1"]
    assert [k["tag"] for k in kids] == ["n=2", "n=2"]


# ---------------------------------------------------------------------------
# Zero / negative counts behave sanely (empty list, not an error)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count", [0, -3])
def test_zero_or_negative_count_yields_no_children(count):
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"repeat": count, "child": {"type": "rect", "id": "r{{i}}"}},
            ],
        }],
    }
    assert _children(expand_components(config)) == []


def test_repeat_preserves_sibling_children_and_order():
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"type": "label", "id": "head"},
                {"repeat": 2, "child": {"type": "rect", "id": "r{{i}}"}},
                {"type": "label", "id": "tail"},
            ],
        }],
    }
    kids = _children(expand_components(config))
    assert [k["id"] for k in kids] == ["head", "r0", "r1", "tail"]


# ---------------------------------------------------------------------------
# Layout keys apply per-copy
# ---------------------------------------------------------------------------

def test_layout_keys_apply_per_copy():
    config = {
        "widgets": [{
            "type": "grid", "id": "g", "children": [
                {"repeat": 3, "as": "i",
                 "child": {"type": "rect", "id": "c{{i}}",
                           "row": 0, "column": "{{i}}", "w": 20}},
            ],
        }],
    }
    kids = _children(expand_components(config))
    assert [(k["row"], k["column"]) for k in kids] == [(0, 0), (0, 1), (0, 2)]
    assert all(k["w"] == 20 for k in kids)     # a literal layout key per copy


# ---------------------------------------------------------------------------
# Composition with {{param}} via use:
# ---------------------------------------------------------------------------

def test_repeat_inside_a_use_component_resolves_param_and_index():
    config = {
        "components": {
            "bar": {
                "params": ["n", "prefix"],
                "widget": {
                    "type": "stack", "id": "bar_root", "direction": "row",
                    "children": [
                        {"repeat": "{{n}}", "as": "i",
                         "child": {"type": "rect",
                                   "id": "{{prefix}}_{{i}}",
                                   "tag": "{{i}}/{{count}}"}},
                    ],
                },
            },
        },
        "widgets": [{"use": "bar", "n": 3, "prefix": "seg"}],
    }
    out = expand_components(config)
    root = out["widgets"][0]
    assert root["id"] == "bar_root"
    kids = root["children"]
    assert [k["id"] for k in kids] == ["seg_0", "seg_1", "seg_2"]
    # {{count}} == the resolved repeat count (3), NOT the param name
    assert [k["tag"] for k in kids] == ["0/3", "1/3", "2/3"]


def test_param_only_child_still_expands_without_index():
    """A component that uses repeat over a param count but whose child only
    references params (no {{i}}) still stamps N identical copies."""
    config = {
        "components": {
            "dots": {
                "params": ["n"], "defaults": {"n": 2},
                "widget": {
                    "type": "stack", "id": "root", "children": [
                        {"repeat": "{{n}}",
                         "child": {"type": "circle", "fill_color": "#fff"}},
                    ],
                },
            },
        },
        "widgets": [{"use": "dots"}],
    }
    kids = expand_components(config)["widgets"][0]["children"]
    assert len(kids) == 2
    assert all(k["fill_color"] == "#fff" for k in kids)


# ---------------------------------------------------------------------------
# Nested repeat (grid): correct index + {{count}} scoping per level
# ---------------------------------------------------------------------------

def test_nested_repeat_grid():
    config = {
        "widgets": [{
            "type": "stack", "id": "grid", "direction": "column",
            "children": [
                {"repeat": 2, "as": "r",
                 "child": {
                     "type": "stack", "id": "row_{{r}}", "direction": "row",
                     "children": [
                         {"repeat": 3, "as": "c",
                          "child": {"type": "rect",
                                    "id": "cell_{{r}}_{{c}}",
                                    # inner {{count}} must be the INNER count
                                    "cols": "{{count}}"}},
                     ],
                 }},
            ],
        }],
    }
    rows = _children(expand_components(config))
    assert [r["id"] for r in rows] == ["row_0", "row_1"]
    ids = [[c["id"] for c in r["children"]] for r in rows]
    assert ids == [["cell_0_0", "cell_0_1", "cell_0_2"],
                   ["cell_1_0", "cell_1_1", "cell_1_2"]]
    # inner {{count}} resolved to the inner repeat count (3), not outer (2);
    # exact-token "{{count}}" is a typed int passthrough
    inner_counts = {c["cols"] for r in rows for c in r["children"]}
    assert inner_counts == {3}


# ---------------------------------------------------------------------------
# Non-repeat / no-component configs are untouched (legacy fast path)
# ---------------------------------------------------------------------------

def test_config_without_components_or_repeat_is_returned_unchanged():
    config = {"widgets": [{"type": "rect", "id": "r", "fill_color": "#abc"}]}
    same = expand_components(config)
    assert same is config
    assert same["widgets"][0]["fill_color"] == "#abc"


def test_repeat_works_without_a_components_block():
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"repeat": 2, "child": {"type": "rect", "id": "r{{i}}"}},
            ],
        }],
    }
    kids = _children(expand_components(config))
    assert [k["id"] for k in kids] == ["r0", "r1"]


def test_bad_count_raises():
    config = {
        "widgets": [{
            "type": "stack", "id": "s", "children": [
                {"repeat": "{{unfilled}}",
                 "child": {"type": "rect", "id": "r{{i}}"}},
            ],
        }],
    }
    with pytest.raises(ValueError):
        expand_components(config)
