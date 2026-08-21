"""
Lottie document model + animated-value evaluation ([SPEC:CXC-LOTTIE]).

Everything here is DETERMINISTIC pure arithmetic: keyframe lookup,
fixed-iteration cubic-bezier easing solves, and linear interpolation.
No wall clocks, no platform math beyond IEEE-754 +-*/ (bit-identical
across macOS/RPi/Minnow), so a frame value is a pure function of
(document, frame) — the property [CXC-LOT-DETERMINISM] rests on.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


class LottieError(ValueError):
    """The bytes/dict are not a usable Lottie document."""


# ---------------------------------------------------------------------------
# Temporal easing — CSS-style cubic bezier (0,0) (x1,y1) (x2,y2) (1,1)
# ---------------------------------------------------------------------------

def _cubic(t: float, a: float, b: float) -> float:
    """Cubic bezier coordinate with P0=0, P3=1 and control values a, b."""
    mt = 1.0 - t
    return 3.0 * mt * mt * t * a + 3.0 * mt * t * t * b + t * t * t


def bezier_ease(u: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Progress through a cubic-bezier timing function at linear time *u*.

    Solved by fixed-count bisection (26 iterations): pure arithmetic,
    no tolerance-dependent early exit -> bit-identical everywhere.
    """
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    if x1 == y1 and x2 == y2:           # linear fast path
        return u
    lo, hi = 0.0, 1.0
    for _ in range(26):
        mid = (lo + hi) * 0.5
        if _cubic(mid, x1, x2) < u:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) * 0.5
    return _cubic(t, y1, y2)


def _handle(h: Any, index: int, default: float) -> float:
    """Extract one easing-handle coordinate (bodymovin writes scalars OR
    per-dimension lists)."""
    if not isinstance(h, dict):
        return default
    v = h.get("x" if index == 0 else "y", default)
    if isinstance(v, (list, tuple)):
        return float(v[0]) if v else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_list(a: List[float], b: List[float], t: float) -> List[float]:
    n = min(len(a), len(b))
    return [a[i] + (b[i] - a[i]) * t for i in range(n)]


def _as_list(v: Any) -> List[float]:
    if isinstance(v, (list, tuple)):
        return [float(x) for x in v]
    return [float(v)]


# ---------------------------------------------------------------------------
# Animated values
# ---------------------------------------------------------------------------

class Animated:
    """A Lottie animatable property ({a, k}) evaluated at a frame.

    vec_at(f) returns a list of floats; scalar_at(f) its first component.
    Handles static values, keyframes (modern s-only and legacy s/e),
    hold keyframes, temporal bezier easing, and spatial to/ti bezier
    interpolation for positions.
    """

    __slots__ = ("static", "value", "kfs")

    def __init__(self, data: Any, default: Any = 0.0) -> None:
        if data is None:
            self.static, self.value, self.kfs = True, _as_list(default), None
            return
        k = data.get("k", default) if isinstance(data, dict) else data
        if isinstance(k, list) and k and isinstance(k[0], dict) and "t" in k[0]:
            self.static, self.value = False, None
            self.kfs = k
        else:
            self.static, self.value, self.kfs = True, _as_list(k), None

    # -- evaluation ---------------------------------------------------------

    def _kf_start(self, kf: dict) -> List[float]:
        s = kf.get("s")
        return _as_list(s if s is not None else 0.0)

    def vec_at(self, f: float) -> List[float]:
        if self.static:
            return self.value                       # type: ignore[return-value]
        kfs = self.kfs                              # type: ignore[assignment]
        if f <= float(kfs[0].get("t", 0)):
            return self._kf_start(kfs[0])
        for j in range(len(kfs) - 1):
            k0, k1 = kfs[j], kfs[j + 1]
            t0, t1 = float(k0.get("t", 0)), float(k1.get("t", 0))
            if not (t0 <= f < t1):
                continue
            s0 = self._kf_start(k0)
            if k0.get("h"):
                return s0                           # hold keyframe
            e = k0.get("e")
            s1 = _as_list(e) if e is not None else self._kf_start(k1)
            span = t1 - t0
            u = (f - t0) / span if span > 0 else 1.0
            eased = bezier_ease(
                u,
                _handle(k0.get("o"), 0, 0.0), _handle(k0.get("o"), 1, 0.0),
                _handle(k0.get("i"), 0, 1.0), _handle(k0.get("i"), 1, 1.0))
            to, ti = k0.get("to"), k0.get("ti")
            if to is not None and ti is not None and len(s0) >= 2:
                # spatial bezier: P0=s0, P1=s0+to, P2=s1+ti, P3=s1
                tov, tiv = _as_list(to), _as_list(ti)
                out: List[float] = []
                mt = 1.0 - eased
                for i in range(min(len(s0), len(s1))):
                    p0 = s0[i]
                    p1 = s0[i] + (tov[i] if i < len(tov) else 0.0)
                    p2 = s1[i] + (tiv[i] if i < len(tiv) else 0.0)
                    p3 = s1[i]
                    out.append(mt * mt * mt * p0 + 3 * mt * mt * eased * p1
                               + 3 * mt * eased * eased * p2
                               + eased * eased * eased * p3)
                return out
            return _lerp_list(s0, s1, eased)
        # past the last keyframe: its start value holds
        return self._kf_start(kfs[-1])

    def scalar_at(self, f: float) -> float:
        v = self.vec_at(f)
        return v[0] if v else 0.0


class AnimatedShape:
    """An animatable bezier shape ({a, k} where values are {c, v, i, o}).
    Vertices and tangents interpolate component-wise between keyframes."""

    __slots__ = ("static", "value", "kfs")

    def __init__(self, data: Any) -> None:
        k = data.get("k") if isinstance(data, dict) else data
        if isinstance(k, list) and k and isinstance(k[0], dict) and "t" in k[0]:
            self.static, self.value, self.kfs = False, None, k
        else:
            self.static, self.value, self.kfs = True, k, None

    @staticmethod
    def _kf_shape(kf: dict) -> Optional[dict]:
        s = kf.get("s")
        if isinstance(s, list) and s:
            return s[0]
        return s if isinstance(s, dict) else None

    @staticmethod
    def _blend(a: dict, b: dict, t: float) -> dict:
        out = {"c": a.get("c", False)}
        for key in ("v", "i", "o"):
            av, bv = a.get(key) or [], b.get(key) or []
            n = min(len(av), len(bv))
            out[key] = [_lerp_list(_as_list(av[i]), _as_list(bv[i]), t)
                        for i in range(n)]
        return out

    def at(self, f: float) -> Optional[dict]:
        if self.static:
            return self.value                       # type: ignore[return-value]
        kfs = self.kfs                              # type: ignore[assignment]
        if f <= float(kfs[0].get("t", 0)):
            return self._kf_shape(kfs[0])
        for j in range(len(kfs) - 1):
            k0, k1 = kfs[j], kfs[j + 1]
            t0, t1 = float(k0.get("t", 0)), float(k1.get("t", 0))
            if not (t0 <= f < t1):
                continue
            s0 = self._kf_shape(k0)
            if k0.get("h") or s0 is None:
                return s0
            s1 = self._kf_shape(k1) or s0
            span = t1 - t0
            u = (f - t0) / span if span > 0 else 1.0
            eased = bezier_ease(
                u,
                _handle(k0.get("o"), 0, 0.0), _handle(k0.get("o"), 1, 0.0),
                _handle(k0.get("i"), 0, 1.0), _handle(k0.get("i"), 1, 1.0))
            return self._blend(s0, s1, eased)
        return self._kf_shape(kfs[-1])


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

class LottieAnimation:
    """A parsed Lottie document: header fields, layers, assets, markers.

    Layers/shapes stay as raw dicts (the renderer walks them); this class
    owns the header, marker resolution, and segment math.
    """

    def __init__(self, data: dict) -> None:
        if not isinstance(data, dict) or "layers" not in data:
            raise LottieError("not a Lottie document (no layers)")
        self.data = data
        self.fr = float(data.get("fr", 60.0)) or 60.0
        self.ip = float(data.get("ip", 0.0))
        self.op = float(data.get("op", self.ip + 1.0))
        self.w = float(data.get("w", 512.0)) or 512.0
        self.h = float(data.get("h", 512.0)) or 512.0
        self.name = str(data.get("nm", ""))
        self.layers: List[dict] = list(data.get("layers") or [])
        self.assets: Dict[str, dict] = {
            str(a.get("id")): a for a in (data.get("assets") or [])
            if isinstance(a, dict)}
        # markers: name -> (start, end) frame range
        self.markers: Dict[str, Tuple[float, float]] = {}
        marker_list = data.get("markers") or []
        for i, m in enumerate(marker_list):
            if not isinstance(m, dict):
                continue
            name = str(m.get("cm", "")).strip()
            if not name:
                continue
            start = float(m.get("tm", 0.0))
            dr = float(m.get("dr", 0.0))
            if dr > 0:
                end = start + dr
            else:                                   # zero-duration marker:
                nxt = [float(x.get("tm", 0.0)) for x in marker_list[i + 1:]
                       if isinstance(x, dict)]
                end = min(nxt) if nxt else self.op  # runs to the next/op
            self.markers[name] = (start, end)

    # -- constructors ---------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> "LottieAnimation":
        try:
            return cls(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise LottieError(f"not Lottie JSON: {e}") from e

    # -- segments ([CXC-LOT-PLAYBACK] playSegments) ---------------------------

    def resolve_segment(self, seg: Any) -> Optional[Tuple[float, float]]:
        """Resolve a playSegments spec to a (start, end) frame range.

        Accepts a marker name (str), an [a, b] pair (frames, or marker
        names per element), or a "a,b" string.  Returns None when it
        cannot be resolved (playback falls back to the full [ip, op]).
        """
        if seg is None:
            return None
        if isinstance(seg, str):
            if seg in self.markers:
                return self.markers[seg]
            if "," in seg:
                parts = [p.strip() for p in seg.split(",", 1)]
                return self.resolve_segment(parts)
            try:
                return (float(seg), self.op)
            except ValueError:
                return None
        if isinstance(seg, (list, tuple)) and len(seg) >= 2:
            ends: List[float] = []
            for i, part in enumerate(seg[:2]):
                if isinstance(part, str) and part in self.markers:
                    ends.append(self.markers[part][i])
                else:
                    try:
                        ends.append(float(part))
                    except (TypeError, ValueError):
                        return None
            a, b = ends
            return (a, b) if b > a else None
        return None

    def clamp_frame(self, f: float) -> float:
        if f < self.ip:
            return self.ip
        if f > self.op:
            return self.op
        return f
