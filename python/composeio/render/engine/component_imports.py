"""`components_from:` — cross-file component import ([SPEC:CXC-FLEET] [CXC-FC-COMPOSITE]).

THE GAP THIS CLOSES, and why it was load-bearing.  Measured 2026-08-12: every one of the eight
shipped composites is DEFINED AND USED in the same `.compose.yaml`, and no import key existed.  So
a composite could not be reached from a second scene without COPYING it — and copying
`intelligence_pane` into `console.compose.yaml` is precisely the fork [SPEC:CXC-FLEET] exists to
prevent.  The consequence was not academic: it made FC-C1/FC-C12/FC-C13 provable only against a
demo scene, so the surface David actually launches could not satisfy its own mandatory glass
legs.  A blocker that makes an acceptance criterion unreachable is the headline, not a footnote.

Ratified as in scope by composeux-architect, 2026-08-12: the spec requires the console to render
N panes; cross-file import is the mechanism that makes one definition reachable from two scenes;
therefore it is in scope by implication.  An architect's mechanism call, not a product call.

    components_from:
      - ../../python/composeio/render/examples/components/intelligence_pane.compose.yaml

WHAT IS IMPORTED, AND WHAT IS DELIBERATELY NOT.  ONLY the `components:` map.  An imported file is
a full, runnable scene in its own right — it has its own `design:`, `app:`, `namespaces:` and
`widgets:` (that is how a composite ships with its own demo/proof surface).  Importing any of that
would mean opening a component library silently pulled in somebody else's window size, app id,
namespace declarations and widgets.  The import is a LIBRARY read, not an include.

THE PHASE IS THE POINT.  This runs immediately BEFORE `expand_components()`, in the SAME load-time
pass, so the two-phase split (COMP-3) is completely unchanged: `{{param}}` still resolves at LOAD
by the expander, `${ns}` is still left for RUNTIME.  An imported component is indistinguishable
from a locally-declared one by the time the expander sees it — which is the property that keeps
this from being a second component mechanism ([CXC-FC-ANTI]).

FOUR REFUSALS, because a scene that renders empty is the class this platform keeps paying for:

  1. a MISSING or unreadable import raises at parse time (never "no widgets, no error");
  2. a CYCLE raises a named error rather than recursing into a crash;
  3. an import path that ESCAPES the allowed roots raises — a scene may not turn into an
     arbitrary filesystem read;
  4. a file that parses but declares NO `components:` raises — importing a library that
     contributes nothing is always a mistake, and it fails later and further away.

LOCAL WINS.  A scene's own `components:` entry overrides an imported one of the same name, and
that is the only silent shadowing permitted: it is how a scene specialises a library shape without
editing the library.  Import-vs-import collision is REFUSED, because there is no local declaration
to say which was meant.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger("composeio.render.component_imports")

IMPORT_KEY = "components_from"
# The roots an import may resolve inside.  A `.compose.yaml` is authored content, not a trusted
# program, and `components_from: /etc/passwd` must not be a way to read a file into a parse error
# message.  The importing scene's own directory is always allowed; the repo root covers the
# component library, which is the actual use.
_ALLOWED_ENV = "COMPOSEIO_COMPONENT_ROOTS"


class ComponentImportError(ValueError):
    """A `components_from:` import could not be honoured.

    Always RAISED, never logged-and-skipped.  A composite that fails to import leaves every
    `{use: ...}` site unresolved, and an unresolved `use:` renders a PlaceholderWidget — a scene
    that looks like a layout bug three layers away from its actual cause.
    """


def _repo_root() -> str:
    """`<repo>`, from this file (`<repo>/python/composeio/render/engine/…`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def allowed_roots(base_dir: str) -> List[str]:
    """Directories an import may resolve inside — the importing scene's own dir, the repo, and
    anything an operator adds via `COMPOSEIO_COMPONENT_ROOTS` (os.pathsep-separated)."""
    roots = [os.path.abspath(base_dir), _repo_root()]
    extra = os.environ.get(_ALLOWED_ENV, "").strip()
    if extra:
        roots.extend(os.path.abspath(os.path.expanduser(p))
                     for p in extra.split(os.pathsep) if p.strip())
    return roots


def _within(path: str, roots: List[str]) -> bool:
    real = os.path.realpath(path)
    for root in roots:
        rr = os.path.realpath(root)
        if real == rr or real.startswith(rr + os.sep):
            return True
    return False


def resolve_import_path(spec: str, base_dir: str) -> str:
    """One `components_from:` entry -> an absolute path, validated.

    Relative to the IMPORTING SCENE, not to the launch cwd: a scene must mean the same thing
    however it is launched, and a cwd-relative import would resolve differently from the console
    supervisor than from a bare `python3 -m ...runtime` ([CXC-R-ENGINE-ASSETS] applies the same
    rule to bundled assets).
    """
    raw = str(spec or "").strip()
    if not raw:
        raise ComponentImportError(
            f"{IMPORT_KEY}: empty import entry — an import that names nothing cannot be "
            f"honoured, and skipping it would leave every `use:` of the expected component "
            f"rendering a placeholder")
    if os.path.isabs(raw):
        candidate = raw
    else:
        candidate = os.path.join(base_dir, raw)
    candidate = os.path.abspath(candidate)
    if not _within(candidate, allowed_roots(base_dir)):
        raise ComponentImportError(
            f"{IMPORT_KEY}: {raw!r} resolves to {candidate!r}, outside the allowed roots "
            f"{allowed_roots(base_dir)}. A scene may import a component library; it may not "
            f"become an arbitrary filesystem read. Set {_ALLOWED_ENV} to widen this "
            f"deliberately.")
    return candidate


def load_components(path: str) -> Dict[str, dict]:
    """The `components:` map from one imported file — and nothing else from it."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except OSError as exc:
        raise ComponentImportError(
            f"{IMPORT_KEY}: cannot read {path!r} ({type(exc).__name__}: {exc}). Refusing to "
            f"parse a scene whose components are missing — every `use:` of them would silently "
            f"render a placeholder.") from exc
    except yaml.YAMLError as exc:
        raise ComponentImportError(
            f"{IMPORT_KEY}: {path!r} is not valid YAML ({exc}).") from exc
    if not isinstance(cfg, dict):
        raise ComponentImportError(
            f"{IMPORT_KEY}: {path!r} is not a .compose.yaml mapping.")
    components = cfg.get("components")
    if not isinstance(components, dict) or not components:
        raise ComponentImportError(
            f"{IMPORT_KEY}: {path!r} declares no `components:` map, so importing it contributes "
            f"nothing. This is refused rather than ignored because the symptom of an empty "
            f"import appears at the `use:` site, far from the mistake.")
    return components


def resolve_component_imports(config: dict, base_dir: Optional[str],
                              _seen: Optional[List[str]] = None) -> dict:
    """Merge every `components_from:` import into `config['components']` and return `config`.

    LOCAL WINS: a scene's own `components:` entry overrides an imported one of the same name —
    the only silent shadowing permitted, because it is how a scene specialises a library shape
    without editing the library.  Import-vs-import collision is REFUSED: with two libraries
    claiming one name and no local declaration to arbitrate, choosing either is a guess.

    TRANSITIVE, and cycle-safe: an imported library may itself import.  `_seen` is the resolution
    STACK, so a cycle is named in the order it was walked instead of hitting Python's recursion
    limit — a `RecursionError` from a YAML parse is a diagnosis nobody can act on.
    """
    specs = config.get(IMPORT_KEY)
    if specs is None:
        return config
    if isinstance(specs, str):
        specs = [specs]
    if not isinstance(specs, list):
        raise ComponentImportError(
            f"{IMPORT_KEY}: expected a list of paths, got {type(specs).__name__}")
    if not specs:
        config.pop(IMPORT_KEY, None)
        return config
    if not base_dir:
        raise ComponentImportError(
            f"{IMPORT_KEY} is declared but this config was parsed from a dict with no known "
            f"directory, so a relative import has no anchor. Parse the scene with "
            f"`parse_compose_yaml(path)`, or pass `base_dir=` to `parse_widget_tree`.")

    stack = list(_seen or [])
    imported: Dict[str, dict] = {}
    origin: Dict[str, str] = {}

    for spec in specs:
        path = resolve_import_path(spec, base_dir)
        if path in stack:
            chain = " -> ".join(stack + [path])
            raise ComponentImportError(
                f"{IMPORT_KEY}: import CYCLE refused: {chain}. A cycle cannot be resolved to a "
                f"single definition, and following it would recurse until the interpreter gave "
                f"up with a stack trace that names nothing useful.")
        components = load_components(path)

        # transitive: the library may import its own dependencies
        with open(path, "r", encoding="utf-8") as fh:
            sub_cfg = yaml.safe_load(fh) or {}
        if sub_cfg.get(IMPORT_KEY):
            sub = resolve_component_imports(
                {IMPORT_KEY: sub_cfg[IMPORT_KEY], "components": dict(components)},
                os.path.dirname(path), stack + [path])
            components = sub.get("components", components)

        for name, body in components.items():
            if name in imported and origin.get(name) != path:
                raise ComponentImportError(
                    f"{IMPORT_KEY}: component {name!r} is declared by BOTH "
                    f"{origin[name]!r} and {path!r}. With no local declaration to arbitrate, "
                    f"choosing either is a guess — declare it in this scene's own "
                    f"`components:` to say which was meant.")
            imported[name] = body
            origin[name] = path
        logger.debug("%s: imported %d component(s) from %s",
                     IMPORT_KEY, len(components), path)

    local = config.get("components")
    merged = dict(imported)
    if isinstance(local, dict):
        merged.update(local)                 # LOCAL WINS
    config["components"] = merged
    config.pop(IMPORT_KEY, None)             # consumed, like `variables:`
    return config
