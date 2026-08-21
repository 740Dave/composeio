"""composeio.defect_ops.profile — the defect-domain vocabulary + STABLE symptom
identity, and the loader for the `defect` FactLattice profile.

Split out from `core.py` so the identity rule (a symptom -> its DEFECT- node id)
and the lifecycle/edge vocabulary are one small, testable surface. The `defect`
profile itself is `defect-profile.yaml` (a NEW CONSUMER of the ONE FactLattice
engine, [DEC-ONE-FACTLATTICE-PARSER] — never a second engine); this module loads
it and hands it to `factlattice.parse()`/`lint()` exactly as decision_ops hands
the ComposeIO profile.

STABLE symptom identity is the load-bearing idea of the whole registry: a defect
is keyed to WHAT BROKE, not to when it was filed, so a recurrence of the same
symptom dedups onto the SAME node and accumulates its history (the anti-re-dig
gate). `symptom_slug()` is therefore deterministic over the NORMALIZED symptom
text: a human/Intelligence filing "heartbeat get_layout returns a MERGED
snapshot" twice, in any casing/spacing, lands on one id.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import List, Optional

# ---------------------------------------------------------------------------
# The defect lifecycle + edge vocabulary (mirrors defect-profile.yaml; kept in
# code too so the ops layer + tests reference ONE spelling of each token).
# ---------------------------------------------------------------------------
LIFECYCLE: tuple = (
    "reported", "investigating", "fix-attempted", "verified", "recurred",
)
ATTEMPT_OUTCOMES: tuple = ("held", "recurred")
EDGE_KINDS: tuple = ("supersedes", "reopens", "related-to", "investigates")

# ---------------------------------------------------------------------------
# Profile loading (reuse-by-reference: factlattice.load_profile does the work).
# ---------------------------------------------------------------------------
_PROFILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "defect-profile.yaml")


def default_profile_path() -> Optional[str]:
    """The path to the packaged `defect` profile, or None if absent. Overridable
    via $DEFECT_OPS_PROFILE (an explicit path wins; a missing one falls back to
    the packaged file)."""
    env = os.environ.get("DEFECT_OPS_PROFILE")
    if env and os.path.isfile(env):
        return env
    return _PROFILE_FILE if os.path.isfile(_PROFILE_FILE) else None


# ---------------------------------------------------------------------------
# STABLE symptom identity
# ---------------------------------------------------------------------------
# A DEFECT node id is `DEFECT-<TOKENS>-<HASH6>`: up to a few uppercased word
# tokens (human-readable) + a 6-hex-char digest of the FULL normalized symptom
# (collision-resistant, so two genuinely different symptoms that share leading
# words still get distinct ids). Both halves are derived from the SAME
# normalized text, so identity is stable across casing/whitespace/punctuation.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MAX_TOKENS = 5
_HASH_LEN = 6

# Mirror decision_ops.core.NODE_ID_RE shape ([PREFIX-NAME], segments of [A-Z0-9]).
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


def normalize_symptom(symptom: str) -> str:
    """The canonical form a symptom is keyed by: lowercased, punctuation-stripped
    to spaces, whitespace-collapsed. This is the dedup key — two reports whose
    normalized symptom matches are the SAME defect."""
    s = str(symptom or "").lower()
    return " ".join(_TOKEN_RE.findall(s))


def symptom_slug(symptom: str) -> str:
    """The STABLE DEFECT node id for a symptom. Deterministic over the normalized
    text; same symptom (any casing/spacing) -> same id. Always a valid
    [PREFIX-NAME] id (>= 2 segments)."""
    norm = normalize_symptom(symptom)
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:_HASH_LEN].upper()
    tokens = [t.upper() for t in norm.split(" ") if t][:_MAX_TOKENS]
    body = "-".join(tokens)
    node_id = f"DEFECT-{body}-{digest}" if body else f"DEFECT-{digest}"
    assert _ID_RE.match(node_id), f"symptom_slug produced an invalid id: {node_id!r}"
    return node_id


def attempt_node_id(defect_id: str, n: int) -> str:
    """The id of the n-th fix-attempt child of a defect: `<defect_id>-A<n>`
    (1-based). A first-class graph node so the per-attempt narrative + the
    supersedes/reopens edges live in the graph, not a flat table."""
    if n < 1:
        raise ValueError(f"attempt number must be >= 1, got {n}")
    node_id = f"{defect_id}-A{int(n)}"
    if not _ID_RE.match(node_id):
        raise ValueError(f"attempt_node_id produced an invalid id: {node_id!r}")
    return node_id


def is_attempt_id(node_id: str) -> bool:
    """True iff `node_id` is a DEFECT-<slug>-A<n> attempt-child id."""
    return bool(re.search(r"-A[0-9]+$", str(node_id or "")))


def defect_id_of_attempt(attempt_id: str) -> str:
    """The parent DEFECT id for an attempt-child id (strips the trailing -A<n>)."""
    return re.sub(r"-A[0-9]+$", "", str(attempt_id or ""))


__all__ = [
    "LIFECYCLE", "ATTEMPT_OUTCOMES", "EDGE_KINDS",
    "default_profile_path", "normalize_symptom", "symptom_slug",
    "attempt_node_id", "is_attempt_id", "defect_id_of_attempt",
]
