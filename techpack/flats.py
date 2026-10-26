"""Technical flats drawn in code, to scale, from the pack's own measurements.

Claude chooses the garment features (flat_spec) and maps dimension roles to
measurement-table codes. This module turns the base-size values into clean
SVG line drawings, so the proportions always match the spec and the drawings
re-render when someone edits a measurement.
"""

import math
import re
from typing import Dict, List, Optional, Tuple

VW, VH = 560, 680          # SVG viewBox
PAD_X, PAD_TOP, PAD_BOT = 78, 24, 30
INK, POM = "#141414", "#c1121f"
Pt = Tuple[float, float]

ANCHOR_ALIASES = {
    "raglan_seam": "sleeve_seam", "armhole_seam": "sleeve_seam", "sleeve": "sleeve_seam", "shoulder": "sleeve_seam",
    "zip": "closure", "front_closure": "closure", "placket": "closure", "button": "closure", "buttons": "closure",
    "pockets": "pocket", "welt_pocket": "pocket", "kangaroo_pocket": "pocket", "hand_pocket": "pocket",
    "chest_pocket": "pocket", "side_pocket": "pocket", "slant_pocket": "pocket",
    "collar": "neck", "neckline": "neck", "rib_collar": "neck", "neck_rib": "neck",
    "hood_seam": "back_hood_seam", "cb_seam": "back_hood_seam", "label": "neck_label", "main_label": "neck_label",
    "hem_band": "hem", "bottom_hem": "hem", "leg_opening": "hem", "leg_hem": "hem",
    "cuffs": "cuff", "cuff_band": "cuff", "piping": "panel_seam", "panel": "panel_seam", "curved_seam": "panel_seam",
    "waist": "waistband", "waist_band": "waistband", "fly_zip": "fly", "knee_seam": "knee_panel", "cargo": "cargo_pocket",
    "back_pockets": "back_pocket", "drawstring": "drawcord", "cord": "drawcord", "eyelets": "drawcord",
}

DEFAULTS_TOP = dict(body_length=70, chest_width=56, shoulder_width=None, sleeve_length=60, cuff_width=10,
                    neck_width=19, hood_height=33, hood_width=30)
DEFAULTS_BOTTOM = dict(waist_width=40, hip_width=52, front_rise=30, inseam=76, thigh_width=32, knee_width=26,
                       leg_opening=20, waistband_height=4)
NECK_DROP = dict(crew=8.0, v_neck=17.0, hood=6.0, collar=6.0, mock=4.0, none=4.0)


def _num(v) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _norm_anchor(a: str) -> str:
    a = re.sub(r"[^a-z0-9_:]+", "_", str(a or "").strip().lower()).strip("_")
    if a.startswith("p2:"):
        return "p2:" + _norm_anchor(a[3:])
    if ":" in a:
        view, name = a.split(":", 1)
        return f"{view}:{ANCHOR_ALIASES.get(name, name)}"
    return ANCHOR_ALIASES.get(a, a)


# ---------------------------------------------------------------- dimensions

def resolve_dims(pack: dict, template: str, spec: Optional[dict] = None) -> Tuple[Dict[str, float], List[str]]:
    """Base-size dimensions in cm for each role, plus warnings about anything defaulted."""
    spec = spec if spec is not None else (pack.get("flat_spec") or {})
    warnings: List[str] = []
    by_code = {str(m.get("code", "")).strip().upper(): m for m in pack.get("measurements", [])}
    sizes, base = pack.get("sizes", []), pack.get("base_size")
    idx = sizes.index(base) if base in sizes else 0
    factor = 2.54 if str(pack.get("units", "cm")).lower().startswith("in") else 1.0
    out: Dict[str, float] = {}
    for d in spec.get("dims", []):
        role, code = d.get("role"), str(d.get("pom_code", "")).strip().upper()
        m = by_code.get(code)
        vals = (m or {}).get("values", [])
        n = _num(vals[idx]) if idx < len(vals) else None
        if not m:
            warnings.append(f"Flat dimension '{role}' points to measurement '{code}', which isn't in the table; a default was used.")
        elif not n:
            warnings.append(f"Flat dimension '{role}' (measurement {code}) has no numeric base-size value; a default was used.")
        else:
            out[role] = n * factor
    defaults = DEFAULTS_TOP if template == "top" else DEFAULTS_BOTTOM
    # a circumference given instead of a flat width: halve it
    limits = dict(chest_width=85, hem_width=85, waist_width=75, hip_width=90, thigh_width=48)
    for k, lim in limits.items():
        if k in out and out[k] > lim:
            out[k] /= 2
            warnings.append(f"'{k}' looked like a circumference, so it was halved to draw a flat width.")
    for k, v in defaults.items():
        if k == "sleeve_length" and "sleeve_inseam" in out:
            continue
        if k not in out and v is not None:
            out[k] = float(v)
    if template == "top":
        out.setdefault("hem_width", out["chest_width"] * 0.96)
        style = spec.get("sleeve_style")
        out.setdefault("shoulder_width", max(out["chest_width"] * 0.80, out["neck_width"] + 16) if style == "raglan" else out["chest_width"] * (0.98 if style == "drop_shoulder" else 0.84))
    return out, warnings


# ---------------------------------------------------------------- drawing kit

class _Draw:
    """Collects SVG pieces. All geometry is given in cm (y down); this scales to pixels."""

    def __init__(self, s: float, ox: float, oy: float):
        self.s, self.ox, self.oy = s, ox, oy
        self.back: List[str] = []     # drawn first (e.g. hood)
        self.parts: List[str] = []
        self.pom: List[str] = []
        self.call: List[str] = []
        self.anchors: Dict[str, Pt] = {}

    def X(self, x): return self.ox + x * self.s
    def Y(self, y): return self.oy + y * self.s
    def P(self, p: Pt): return f"{self.X(p[0]):.1f},{self.Y(p[1]):.1f}"

    def d(self, cmds) -> str:
        out = []
        for c in cmds:
            out.append(c[0] + " " + " ".join(self.P(p) for p in c[1:]) if len(c) > 1 else c[0])
        return " ".join(out)

    def path(self, cmds, *, dash=False, fill="none", sw=1.6, color=INK, to=None, op=None):
        extra = ' stroke-dasharray="5 3.2"' if dash else ""
        extra += ' stroke-linejoin="round" stroke-linecap="round"'
        if op:
            extra += f' opacity="{op}"'
        (to if to is not None else self.parts).append(f'<path d="{self.d(cmds)}" fill="{fill}" stroke="{color}" stroke-width="{sw}"{extra}/>')

    def line(self, a: Pt, b: Pt, **kw):
        self.path([("M", a), ("L", b)], **kw)

    def rect(self, x0, y0, x1, y1, **kw):
        self.path([("M", (x0, y0)), ("L", (x1, y0)), ("L", (x1, y1)), ("L", (x0, y1)), ("Z",)], **kw)

    def circle(self, c: Pt, r_cm: float, fill="none", sw=1.3):
        self.parts.append(f'<circle cx="{self.X(c[0]):.1f}" cy="{self.Y(c[1]):.1f}" r="{max(1.6, r_cm * self.s):.1f}" fill="{fill}" stroke="{INK}" stroke-width="{sw}"/>')


def mir(cmds):
    """Mirror a command list across the centre line."""
    return [(c[0],) + tuple((-p[0], p[1]) for p in c[1:]) for c in cmds]


def both(dr: _Draw, cmds, **kw):
    dr.path(cmds, **kw)
    dr.path(mir(cmds), **kw)


def offset_pair(dr: _Draw, cmds, gap_cm=0.45, **kw):
    """Draw a seam as two close parallel lines (piping). Offsets the control points sideways."""
    dr.path(cmds, **kw)
    dr.path([(c[0],) + tuple((p[0] - gap_cm, p[1]) for p in c[1:]) for c in cmds], **kw)


def bez(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: float) -> Pt:
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1])


def qbez(p0: Pt, p1: Pt, p2: Pt, t: float) -> Pt:
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def rib_hatch(dr: _Draw, x0: float, x1: float, y0: float, y1: float, step=0.9):
    x = x0 + step / 2
    while x < x1:
        dr.line((x, y0), (x, y1), sw=0.6, op=0.55)
        x += step


# ---------------------------------------------------------------- TOP template

def _sleeve_pts(d, spec, S, U):
    """Outer and inner cuff corners. A real inseam (underarm to cuff) wins over an outer length."""
    th = _theta(d, spec)
    dirv = (math.sin(th), math.cos(th))
    perp = (math.cos(th), -math.sin(th))
    CuW = d["cuff_width"]
    if "sleeve_inseam" in d:
        Ci = (U[0] + d["sleeve_inseam"] * dirv[0], U[1] + d["sleeve_inseam"] * dirv[1])
        Co = (Ci[0] + CuW * perp[0], Ci[1] + CuW * perp[1])
    else:
        Co = (S[0] + d["sleeve_length"] * dirv[0], S[1] + d["sleeve_length"] * dirv[1])
        Ci = (Co[0] - CuW * perp[0], Co[1] - CuW * perp[1])
    Pb = None
    if d.get("bicep_width"):
        Pb = (U[0] + d["bicep_width"] * perp[0], U[1] + d["bicep_width"] * perp[1])
        if spec.get("sleeve_style") in ("raglan", "drop_shoulder"):
            # the shoulder point isn't measured: it sits where the sleeve's outer edge meets the shoulder slope
            k = 0.55 if spec.get("sleeve_style") == "raglan" else 0.40
            dx, dy = Co[0] - Pb[0], Co[1] - Pb[1]
            nx = d["neck_width"] / 2
            den = dy - k * dx
            if abs(den) > 1e-6:
                t = (k * (Pb[0] - nx) - Pb[1]) / den
                if t < 0:
                    S = (Pb[0] + t * dx, Pb[1] + t * dy)
                    Pb = None
    return Co, Ci, dirv, perp, S, Pb


def _top_geom(d, spec):
    style = spec.get("sleeve_style", "set_in")
    CW, BL, SW, NW = d["chest_width"], d["body_length"], d["shoulder_width"], d["neck_width"]
    half_sh = SW / 2 - NW / 2
    sd = max(1.6, min(6.0, half_sh * (0.25 if style == "drop_shoulder" else 0.30)))
    AD = d.get("armhole_drop") or CW * (0.33 if style == "drop_shoulder" else 0.40)
    AD = min(AD, BL * 0.6)
    return (SW / 2, sd), (CW / 2, AD), AD, sd


def _top_extent(d, spec) -> Tuple[float, float, float]:
    """Half-width, top (negative) and bottom of the drawing in cm, for scaling."""
    sleeve = spec.get("sleeve_style") not in ("sleeveless", "none")
    S, U, _, _ = _top_geom(d, spec)
    if sleeve:
        Co, Ci, _, _, S, Pb = _sleeve_pts(d, spec, S, U)
        sx = max(Co[0], Ci[0], S[0], Pb[0] if Pb else 0)
    else:
        sx = S[0]
    half = max(sx, d["chest_width"] / 2, d["hem_width"] / 2)
    hood = spec.get("neckline") == "hood"
    top = -(d["hood_height"] + 1.5) if hood else (-5.5 if spec.get("neckline") == "mock" else -1.5)
    if hood:
        half = max(half, d["hood_width"] / 2)
    return half, top, d["body_length"] + 4.0


def _theta(d, spec) -> float:
    style = spec.get("sleeve_style")
    sl = d.get("sleeve_length") or d.get("sleeve_inseam", 48) + 10
    base = {"raglan": 15, "drop_shoulder": 11}.get(style, 15)
    if sl < 34:
        base = 62
    elif sl < 48:
        base = 40
    return math.radians(base)


def draw_top(dr: _Draw, d: Dict[str, float], spec: dict, view: str):
    front = view == "front"
    CW, BL, HW, SW = d["chest_width"], d["body_length"], d["hem_width"], d["shoulder_width"]
    CuW, NW = d["cuff_width"], d["neck_width"]
    neck = spec.get("neckline", "crew")
    style = spec.get("sleeve_style", "set_in")
    hood = neck == "hood"
    ND = d.get("front_neck_drop", NECK_DROP.get(neck, 6.0)) if front else 2.4
    if neck == "v_neck" and not front:
        ND = 3.0
    N = (NW / 2, 0.0)
    S, U, AD, sd = _top_geom(d, spec)
    Hm = (HW / 2, BL)
    has_sleeve = style not in ("sleeveless", "none")
    Co, Ci, dirv, perp, S, Pb = _sleeve_pts(d, spec, S, U) if has_sleeve else (None, None, None, None, S, None)
    # --- hood (behind the body)
    HH, HDW = d["hood_height"], d["hood_width"]
    if hood:
        hood_out = [("M", (-NW / 2 - 0.6, 1.2)),
                    ("C", (-HDW / 2 - 0.5, -HH * 0.18), (-HDW / 2, -HH * 0.95), (0, -HH)),
                    ("C", (HDW / 2, -HH * 0.95), (HDW / 2 + 0.5, -HH * 0.18), (NW / 2 + 0.6, 1.2)), ("Z",)]
        dr.path(hood_out, fill="#fff", to=dr.back)
        if front:
            ow = HDW * 0.34
            opening = [("M", (-ow * 0.55, ND * 0.8)),
                       ("C", (-ow * 1.25, -HH * 0.12), (-ow * 1.05, -HH * 0.80), (0, -HH * 0.90)),
                       ("C", (ow * 1.05, -HH * 0.80), (ow * 1.25, -HH * 0.12), (ow * 0.55, ND * 0.8))]
            dr.path(opening, fill="#f4f4f4", to=dr.back)
            inner = [("M", (-ow * 0.55 - 1.0, ND * 0.8 + 0.2)),
                     ("C", (-ow * 1.37, -HH * 0.12), (-ow * 1.15, -HH * 0.86), (0, -HH * 0.97)),
                     ("C", (ow * 1.15, -HH * 0.86), (ow * 1.37, -HH * 0.12), (ow * 0.55 + 1.0, ND * 0.8 + 0.2))]
            dr.path(inner, dash=True, sw=1.1, to=dr.back)
        else:
            dr.line((0, -HH), (0, 0.5), to=dr.back)
            dr.line((-0.5, -HH + 0.4), (-0.5, 0.5), dash=True, sw=1.0, to=dr.back)
            dr.line((0.5, -HH + 0.4), (0.5, 0.5), dash=True, sw=1.0, to=dr.back)
        dr.anchors["hood"] = (-HDW * 0.40, -HH * 0.55)
        dr.anchors["back_hood_seam"] = (0, -HH * 0.45) if not front else (0, -HH * 0.45)
    # --- body outline
    if neck == "v_neck" and front:
        neck_cmds = [("L", (0, ND)), ("L", (-NW / 2, 0))]
    else:
        neck_cmds = [("C", (NW * 0.47, ND * 0.80), (NW * 0.20, ND), (0, ND)), ("C", (-NW * 0.20, ND), (-NW * 0.47, ND * 0.80), (-NW / 2, 0))]
    if has_sleeve:
        pbl = [("L", (-Pb[0], Pb[1]))] if Pb else []
        pbr = [("L", (Pb[0], Pb[1]))] if Pb else []
        left = [("M", (-N[0], N[1])), ("L", (-S[0], S[1]))] + pbl + [("L", (-Co[0], Co[1])), ("L", (-Ci[0], Ci[1])), ("L", (-U[0], U[1]))]
        right = [("L", (U[0], U[1])), ("L", (Ci[0], Ci[1])), ("L", (Co[0], Co[1]))] + pbr + [("L", (S[0], S[1])), ("L", (N[0], N[1]))]
    else:
        arm = [(S[0] - 1.2, AD * 0.55)]
        left = [("M", (-N[0], N[1])), ("L", (-S[0], S[1])), ("Q", (-(S[0] - 2.5), AD * 0.55), (-U[0] + 1.0, AD)), ("L", (-U[0], AD + 0.01))]
        right = [("L", (U[0], AD + 0.01)), ("L", (U[0] - 1.0, AD)), ("Q", (S[0] - 2.5, AD * 0.55), (S[0], S[1])), ("L", (N[0], N[1]))]
    hem_sag = 1.3 if spec.get("hem_style") == "curved" else 0.0
    hem = [("L", (-Hm[0], Hm[1])), ("Q", (0, BL + 2 * hem_sag), (Hm[0], Hm[1]))] if hem_sag else [("L", (-Hm[0], Hm[1])), ("L", (Hm[0], Hm[1]))]
    dr.path(left + hem + right + neck_cmds + [("Z",)], fill="#fff", sw=1.9)
    # --- neckline details
    if neck == "crew":
        c2 = [("M", (-NW / 2 + 0.5, 0.2)), ("C", (-NW * 0.42, ND * 0.62), (-NW * 0.19, ND - 2.4), (0, ND - 2.4)),
              ("C", (NW * 0.19, ND - 2.4), (NW * 0.42, ND * 0.62), (NW / 2 - 0.5, 0.2))]
        if not front:
            c2 = [("M", (-NW / 2 + 0.5, 0.2)), ("Q", (0, ND + 0.3), (NW / 2 - 0.5, 0.2))]
        dr.path(c2, dash=True, sw=1.2)
    elif neck == "v_neck" and front:
        dr.path([("M", (-NW / 2 + 0.7, 0.2)), ("L", (0, ND - 2.6)), ("L", (NW / 2 - 0.7, 0.2))], dash=True, sw=1.2)
    elif neck == "mock":
        dr.path([("M", (-NW / 2, 0)), ("L", (-NW / 2 + 0.9, -5)), ("L", (NW / 2 - 0.9, -5)), ("L", (NW / 2, 0))], fill="#fff", sw=1.7)
        dr.line((-NW / 2 + 0.5, -2.6), (NW / 2 - 0.5, -2.6), dash=True, sw=1.1)
    elif neck == "collar":
        dr.path([("M", (-NW / 2, 0)), ("L", (-NW / 2 - 1.2, -3.2)), ("L", (-1.2, ND * 0.9)), ("L", (0, ND))], fill="#fff", sw=1.5)
        dr.path([("M", (NW / 2, 0)), ("L", (NW / 2 + 1.2, -3.2)), ("L", (1.2, ND * 0.9)), ("L", (0, ND))], fill="#fff", sw=1.5)
    elif neck == "hood" and front:
        dr.path([("M", (-NW / 2 + 0.4, 0.2)), ("C", (-NW * 0.4, ND * 0.7), (-NW * 0.17, ND - 1.5), (0, ND - 1.5)),
                 ("C", (NW * 0.17, ND - 1.5), (NW * 0.4, ND * 0.7), (NW / 2 - 0.4, 0.2))], dash=True, sw=1.1)
    dr.anchors["neck"] = (-NW * 0.36, ND * 0.62) if neck != "hood" else (-NW * 0.30, ND * 0.55)
    # --- sleeve seams
    sleeve_seam_pt = None
    piped = spec.get("piped_seams")
    if has_sleeve and style == "raglan":
        a = (NW / 2 + 0.7, 0.9)
        c = ((a[0] + U[0]) * 0.5 - 2.2, (a[1] + U[1]) * 0.5 - 0.5)
        seam = [("M", a), ("Q", c, (U[0] - 0.4, U[1]))]
        (offset_pair(dr, seam, 0.4, sw=1.3) if piped else dr.path(seam, sw=1.4))
        (offset_pair(dr, mir(seam), -0.4, sw=1.3) if piped else dr.path(mir(seam), sw=1.4))
        sleeve_seam_pt = qbez(a, c, (U[0] - 0.4, U[1]), 0.5)
    elif has_sleeve and style == "drop_shoulder":
        seam = [("M", S), ("Q", (S[0] + 0.4, AD * 0.5), (U[0], U[1] - 0.3))]
        both(dr, seam, sw=1.4)
        sleeve_seam_pt = qbez(S, (S[0] + 0.4, AD * 0.5), (U[0], U[1] - 0.3), 0.5)
    elif has_sleeve:
        seam = [("M", S), ("Q", (S[0] - 3.4, (S[1] + U[1]) * 0.5), (U[0] - 0.3, U[1]))]
        both(dr, seam, sw=1.4)
        sleeve_seam_pt = qbez(S, (S[0] - 3.4, (S[1] + U[1]) * 0.5), (U[0] - 0.3, U[1]), 0.5)
    if sleeve_seam_pt:
        dr.anchors["sleeve_seam"] = (-sleeve_seam_pt[0], sleeve_seam_pt[1])
    if has_sleeve:
        dr.anchors["side_seam"] = (-(U[0] + Hm[0]) / 2 - 0.0, (AD + BL) / 2)
    # --- cuffs
    cuff = spec.get("cuff_style", "plain")
    if has_sleeve and cuff != "none":
        ch = {"ribbed": 6.0, "elastic": 4.5, "plain": 3.0}[cuff]
        for sgn in (-1, 1):
            o = (sgn * (Co[0] - ch * dirv[0]), Co[1] - ch * dirv[1])
            vin = (Ci[0] - U[0], Ci[1] - U[1]); L = math.hypot(*vin); vin = (vin[0] / L, vin[1] / L)
            i = (sgn * (Ci[0] - ch * vin[0]), Ci[1] - ch * vin[1])
            if cuff == "plain":
                dr.line(o, i, dash=True, sw=1.2)
            else:
                dr.line(o, i, sw=1.4)
                if cuff == "ribbed":
                    for k in range(1, 9):
                        t = k / 9
                        a = (o[0] + (i[0] - o[0]) * t, o[1] + (i[1] - o[1]) * t)
                        b = (a[0] + sgn * (-ch * 0.9 * dirv[0]) * 1, a[1] - ch * 0.9 * dirv[1])
                        dr.line(a, (a[0] + sgn * ch * 0.85 * dirv[0], a[1] + ch * 0.85 * dirv[1]), sw=0.6, op=0.55)
                else:
                    mid = ((o[0] + i[0]) / 2, (o[1] + i[1]) / 2)
                    dr.line((o[0] + sgn * 0.9 * dirv[0], o[1] + 0.9 * dirv[1]), (i[0] + sgn * 0.9 * dirv[0], i[1] + 0.9 * dirv[1]), dash=True, sw=1.0)
        dr.anchors["cuff"] = (-(Co[0] + Ci[0]) / 2 + 0.6, (Co[1] + Ci[1]) / 2 - ch * 0.4)
    # --- hem band
    hs = spec.get("hem_style", "plain")
    hb = {"ribbed": 6.0, "elastic": 4.5, "drawcord": 4.5, "plain": 2.6, "curved": 2.6, "cuffed": 4.0}[hs]
    yb = BL - hb
    xb = HW / 2 - (HW / 2 - CW / 2) * (hb / (BL - AD)) if BL > AD else HW / 2
    if hs in ("ribbed", "elastic", "drawcord"):
        dr.line((-xb, yb), (xb, yb), sw=1.5)
        if hs == "ribbed":
            rib_hatch(dr, -xb, xb, yb + 0.15, BL - 0.15, 1.0)
        else:
            dr.line((-xb + 0.7, BL - 1.1), (xb - 0.7, BL - 1.1), dash=True, sw=1.0)
    else:
        dr.line((-xb, yb), (xb, yb), dash=True, sw=1.2)
    dr.anchors["hem"] = (-(HW / 2) * 0.55, BL - hb / 2)
    # --- closure (front only)
    cl = spec.get("closure", "none")
    if front and cl in ("full_zip", "half_zip", "buttons", "snaps"):
        y_end = BL if cl in ("full_zip", "buttons", "snaps") else ND + 24
        if cl in ("full_zip", "half_zip"):
            dr.line((-0.35, ND), (-0.35, y_end), sw=1.4); dr.line((0.35, ND), (0.35, y_end), sw=1.4)
            dr.line((-1.1, ND), (-1.1, y_end), dash=True, sw=1.0); dr.line((1.1, ND), (1.1, y_end), dash=True, sw=1.0)
            dr.rect(-0.8, ND - 1.0, 0.8, ND + 1.6, fill="#fff", sw=1.2)
            dr.rect(-0.45, ND + 1.6, 0.45, ND + 4.4, fill="#fff", sw=1.2)
            if spec.get("two_way_zip") and cl == "full_zip":
                dr.rect(-0.8, y_end - 2.6, 0.8, y_end - 0.4, fill="#fff", sw=1.2)
                dr.rect(-0.45, y_end - 0.4, 0.45, y_end + 2.2, fill="#fff", sw=1.2)
        else:
            dr.line((-1.7, ND), (-1.7, y_end), dash=True, sw=1.0); dr.line((1.7, ND), (1.7, y_end), dash=True, sw=1.0)
            dr.line((0, ND), (0, y_end), sw=0.7, op=0.5)
            n = max(3, int((y_end - ND) // 11)); gap = (y_end - ND - 4) / n
            for k in range(n + 1):
                dr.circle((0, ND + 2 + k * gap), 0.7, fill="#fff")
        dr.anchors["closure"] = (0.0, ND + (y_end - ND) * 0.42)
    # --- panel seams
    panels = spec.get("panel_seams", [])
    if "curved_side_panels" in panels:
        y0, y1 = AD * 0.95, BL - hb
        p0, p1, p2, p3 = (CW * 0.40, y0), (CW * 0.13, y0 + (y1 - y0) * 0.18), (CW * 0.16, (y0 + y1) / 2 + 3), (HW * 0.37, y1)
        seam = [("M", p0), ("C", p1, p2, p3)]
        if piped:
            offset_pair(dr, seam, 0.4, sw=1.3); offset_pair(dr, mir(seam), -0.4, sw=1.3)
        else:
            both(dr, seam, sw=1.4)
        m = bez(p0, p1, p2, p3, 0.55)
        dr.anchors["panel_seam"] = (m[0] if m[0] < 0 else -m[0], m[1])
    if "yoke_front" in panels and front or "yoke_back" in panels and not front:
        yk = AD * 0.52
        dr.path([("M", (-S[0] + 1.2, yk + 1.0)), ("Q", (0, yk + 3.2), (S[0] - 1.2, yk + 1.0))], sw=1.3)
        dr.anchors["yoke"] = (-S[0] * 0.6, yk + 1.6)
    # --- pockets (front)
    pk = spec.get("pockets", [])
    if front:
        if "kangaroo" in pk:
            y1, y2 = BL * 0.56, BL - hb - 1.0
            xt, xb2 = CW * 0.29, CW * 0.36
            dr.path([("M", (-xt, y1)), ("L", (-xb2, y2)), ("L", (xb2, y2)), ("L", (xt, y1))], sw=1.4)
            dr.line((-xt, y1), (xt, y1), sw=1.4)
            dr.line((-xt + 0.4, y1 + 0.9), (xt - 0.4, y1 + 0.9), dash=True, sw=0.9)
            dr.path([("M", (-xb2 + 0.2, y2 - 5)), ("L", (-xt - 0.5, y1 + 0.2))], sw=1.3)
            dr.path([("M", (xb2 - 0.2, y2 - 5)), ("L", (xt + 0.5, y1 + 0.2))], sw=1.3)
            dr.path([("M", (-xb2 + 1.0, y2 - 4.5)), ("L", (-xt - 0.2, y1 + 1.5))], dash=True, sw=1.0)
            dr.path([("M", (xb2 - 1.0, y2 - 4.5)), ("L", (xt + 0.2, y1 + 1.5))], dash=True, sw=1.0)
            dr.anchors["pocket"] = (-(xt + xb2) / 2, (y1 + y2) / 2)
        if "welt_vertical" in pk:
            y1 = AD + (BL - AD) * 0.30; L = min(16.0, (BL - AD) * 0.34)
            for sgn in (-1, 1):
                x = sgn * CW * 0.285
                dr.rect(x - 0.45, y1, x + 0.45, y1 + L, fill="#fff", sw=1.3)
                dr.rect(x - 0.95, y1 - 0.3, x + 0.95, y1 + L + 0.3, dash=True, sw=0.9)
            dr.anchors["pocket"] = (-CW * 0.285 - 0.9, y1 + L / 2)
        if "chest_patch" in pk:
            x0, y0 = CW * 0.12, AD + 3.0
            dr.rect(x0, y0, x0 + 12, y0 + 13, fill="#fff", sw=1.4)
            dr.rect(x0 + 0.8, y0 + 0.8, x0 + 11.2, y0 + 12.2, dash=True, sw=0.9)
            dr.anchors["pocket"] = (-(x0 + 6), y0 + 6.5)
    # --- drawcord
    if front and spec.get("drawcord") and hood:
        for sgn in (-1, 1):
            x = sgn * 3.3
            dr.circle((x, ND + 1.2), 0.55, fill="#fff")
            dr.path([("M", (x, ND + 1.7)), ("Q", (x + sgn * 0.8, ND + 10), (x + sgn * 0.3, ND + 20))], sw=1.3)
            dr.rect(x + sgn * 0.3 - 0.45, ND + 20, x + sgn * 0.3 + 0.45, ND + 22.4, fill="#fff", sw=1.1)
        dr.anchors["drawcord"] = (-3.6, ND + 12)
    # --- back neck label
    if not front:
        dr.rect(-3.2, ND + 1.0, 3.2, ND + 3.4, fill="#fff", sw=1.1, dash=True)
        dr.anchors["neck_label"] = (-3.2, ND + 2.2)
    # --- POM markers
    marks = {}
    marks["chest_width"] = ("h", (-CW / 2, AD + 4.5), (CW / 2, AD + 4.5))
    marks["body_length"] = ("v", (NW / 2 + 0.2, 0), (NW / 2 + 0.2, BL))
    marks["hem_width"] = ("h", (-HW / 2, BL + 2.6), (HW / 2, BL + 2.6))
    marks["shoulder_width"] = ("h", (-S[0], S[1] - 1.6), (S[0], S[1] - 1.6))
    marks["neck_width"] = ("h", (-NW / 2, -1.1), (NW / 2, -1.1))
    marks["armhole_drop"] = ("v", (NW / 2 + 3.4, 0), (NW / 2 + 3.4, AD))
    if has_sleeve:
        off = 2.4
        a = (S[0] + off * perp[0], S[1] + off * perp[1]); b = (Co[0] + off * perp[0], Co[1] + off * perp[1])
        marks["sleeve_length"] = ("d", a, b)
        ai = (U[0] - 1.9 * perp[0], U[1] - 1.9 * perp[1]); bi = (Ci[0] - 1.9 * perp[0], Ci[1] - 1.9 * perp[1])
        marks["sleeve_inseam"] = ("d", ai, bi)
        pbm = Pb or (U[0] + d.get("bicep_width", 0) * perp[0], U[1] + d.get("bicep_width", 0) * perp[1])
        marks["bicep_width"] = ("d", (U[0] + 2.2 * dirv[0], U[1] + 2.2 * dirv[1]), (pbm[0] + 2.2 * dirv[0], pbm[1] + 2.2 * dirv[1]))
        a2 = (Ci[0] + 1.9 * dirv[0], Ci[1] + 1.9 * dirv[1]); b2 = (Co[0] + 1.9 * dirv[0], Co[1] + 1.9 * dirv[1])
        marks["cuff_width"] = ("d", a2, b2)
    if hood:
        marks["hood_height"] = ("v", (HDW / 2 + 1.8, 0), (HDW / 2 + 1.8, -HH))
        marks["hood_width"] = ("h", (-HDW / 2, -HH * 0.42), (HDW / 2, -HH * 0.42))
    if front:
        marks["front_neck_drop"] = ("v", (-1.4, 0), (-1.4, ND))
    return marks


# ---------------------------------------------------------------- BOTTOM template

def _bottom_extent(d, spec) -> Tuple[float, float, float]:
    half = max(d["hip_width"] / 2, d["waist_width"] / 2) + 0.5
    total = d["front_rise"] + d["inseam"]
    return half, -1.0, total + 4.0


def draw_bottom(dr: _Draw, d: Dict[str, float], spec: dict, view: str):
    front = view == "front"
    WW, HipW, FR, INS = d["waist_width"], d["hip_width"], d["front_rise"], d["inseam"]
    TW, KW, LO, WBH = d["thigh_width"], d["knee_width"], d["leg_opening"], d["waistband_height"]
    shape = spec.get("leg_shape", "straight")
    FRv = FR if front else FR + 3.0
    yh = FR + INS
    yt = FR + 3.0
    yk = FR + INS * 0.5
    g = 0.9
    x_thigh = max(HipW / 2, min(g + TW, HipW / 2 + 3.0))
    c_t = x_thigh - TW / 2
    drift = {"tapered": 1.4, "flared": -1.6, "wide": -0.6}.get(shape, 0.0)
    c_h = c_t - drift
    def axis(y): return c_t + (c_h - c_t) * (y - yt) / (yh - yt)
    out_t = (x_thigh, yt)
    out_k = (axis(yk) + KW / 2, yk)
    out_h = (axis(yh) + LO / 2, yh)
    in_k = (axis(yk) - KW / 2, yk)
    in_h = (axis(yh) - LO / 2, yh)
    in_h = (max(in_h[0], 0.9), yh)
    in_k = (max(in_k[0], 0.8), yk)
    crotch = (0.0, FRv)
    out_cmds = [("M", (-WW / 2, 0)), ("Q", (-(WW / 2 + HipW / 2) / 2 - 0.6, FR * 0.25), (-HipW / 2, FR * 0.55)),
                ("C", (-HipW / 2, FR * 0.85), (-out_t[0] - 0.3, yt - 4), (-out_t[0], yt)),
                ("L", (-out_k[0], out_k[1])), ("L", (-out_h[0], out_h[1])), ("L", (-in_h[0], in_h[1])),
                ("L", (-in_k[0], in_k[1])), ("Q", (-in_k[0] * 0.55, FR + 8), (crotch[0], crotch[1]))]
    right = [("Q", (in_k[0] * 0.55, FR + 8), (in_k[0], in_k[1])), ("L", (in_h[0], in_h[1])), ("L", (out_h[0], out_h[1])),
             ("L", (out_k[0], out_k[1])), ("L", (out_t[0], out_t[1])),
             ("C", (out_t[0] + 0.3, yt - 4), (HipW / 2, FR * 0.85), (HipW / 2, FR * 0.55)),
             ("Q", ((WW / 2 + HipW / 2) / 2 + 0.6, FR * 0.25), (WW / 2, 0)), ("Z",)]
    dr.path(out_cmds + right, fill="#fff", sw=1.9)
    # waistband
    wb = spec.get("waistband", "elastic")
    dr.line((-WW / 2 + 0.2, WBH), (WW / 2 - 0.2, WBH), sw=1.5 if wb != "none" else 1.0)
    if wb in ("elastic", "drawcord"):
        dr.line((-WW / 2 + 0.4, WBH * 0.5), (WW / 2 - 0.4, WBH * 0.5), dash=True, sw=0.9)
    if wb == "belt_loops":
        for x in (-WW * 0.42, -WW * 0.22, WW * 0.22, WW * 0.42):
            dr.rect(x - 0.5, -0.6, x + 0.5, WBH + 1.4, fill="#fff", sw=1.1)
    if wb == "drawcord" and front:
        for sgn in (-1, 1):
            dr.circle((sgn * 2.6, WBH * 0.55), 0.5, fill="#fff")
            dr.path([("M", (sgn * 2.6, WBH * 0.8)), ("Q", (sgn * 3.4, WBH + 8), (sgn * 2.8, WBH + 16))], sw=1.3)
        dr.anchors["drawcord"] = (-3.0, WBH + 9)
    dr.anchors["waistband"] = (-WW * 0.36, WBH / 2)
    # centre seam / fly
    cl = spec.get("closure", "none")
    if front and cl in ("zip_fly", "button_fly"):
        dr.line((0, WBH), (0, FR - 4.5), sw=1.3)
        dr.path([("M", (1.6, WBH)), ("L", (1.6, FR - 8)), ("Q", (1.6, FR - 2.6), (-0.5, FR - 3.6))], dash=True, sw=1.1)
        if cl == "button_fly":
            for k in range(4):
                dr.circle((0.0, WBH + 3 + k * ((FR - 14) / 3)), 0.6, fill="#fff")
        dr.anchors["fly"] = (0.0, FR * 0.5)
    else:
        dr.line((0, WBH), (0, FRv - 2.0), sw=1.3)
        dr.anchors["fly"] = (0.0, FR * 0.5)
    # pockets
    pk = spec.get("pockets", [])
    if front and "side_slant" in pk:
        for sgn in (-1, 1):
            a = (sgn * (WW / 2 - 0.2), WBH + 0.4)
            b = (sgn * (HipW / 2 - 0.5), WBH + 17)
            dr.path([("M", a), ("Q", (sgn * (WW / 2 + 1.0), WBH + 9), b)], sw=1.4)
            dr.path([("M", (a[0] - sgn * 1.0, a[1] + 0.2)), ("Q", (sgn * (WW / 2 - 0.6), WBH + 9), (b[0] - sgn * 1.1, b[1] - 0.4))], dash=True, sw=0.9)
        dr.anchors["pocket"] = (-(WW / 2 + HipW / 2) / 2 + 1.0, WBH + 9)
    if front and "cargo_patch" in pk:
        for sgn in (-1, 1):
            ya = yt + 5; yb = ya + 21
            def outer_at(y):
                if y <= out_k[1]:
                    return out_t[0] + (out_k[0] - out_t[0]) * (y - out_t[1]) / (out_k[1] - out_t[1])
                return out_k[0] + (out_h[0] - out_k[0]) * (y - out_k[1]) / (out_h[1] - out_k[1])
            ox = min(outer_at(ya), outer_at(yb))
            x1, x0 = ox - 1.6, ox - 14.0
            def tx(x): return sgn * x
            dr.path([("M", (tx(x0), ya)), ("L", (tx(x1), ya)), ("L", (tx(x1), yb)), ("L", (tx(x0), yb)), ("Z",)], fill="#fff", sw=1.4)
            dr.path([("M", (tx(x0), ya + 6)), ("L", (tx(x1), ya + 6))], sw=1.3)
            dr.path([("M", (tx(x0 + 0.8), ya + 0.8)), ("L", (tx(x1 - 0.8), ya + 0.8)), ("L", (tx(x1 - 0.8), yb - 0.8)), ("L", (tx(x0 + 0.8), yb - 0.8)), ("Z",)], dash=True, sw=0.8)
        dr.anchors["cargo_pocket"] = (-(ox - 8), yt + 14)
    if not front and ("back_patch" in pk or True):
        if "back_patch" in pk:
            for sgn in (-1, 1):
                x0, x1 = sgn * 3.5, sgn * (HipW / 2 - 4.5)
                ya = WBH + 4.0
                dr.path([("M", (x0, ya)), ("L", (x1, ya)), ("L", (x1, ya + 15)), ("L", ((x0 + x1) / 2, ya + 17.5)), ("L", (x0, ya + 15)), ("Z",)], fill="#fff", sw=1.4)
                dr.line((x0 + sgn * 0.8, ya + 2.2), (x1 - sgn * 0.8, ya + 2.2), dash=True, sw=0.9)
            dr.anchors["back_pocket"] = (-(HipW / 2 - 4.5 + 3.5) / 2, WBH + 11)
    panels = spec.get("panel_seams", [])
    if "yoke_back" in panels and not front:
        dr.path([("M", (-WW / 2 + 0.3, WBH)), ("L", (0, WBH + 5.5)), ("L", (WW / 2 - 0.3, WBH))], sw=1.3)
    if "knee_panel" in panels:
        for k, y in enumerate([yk]):
            xl = lambda s_: s_ * (axis(y) + KW / 2 + 0.0)
            for sgn in (-1, 1):
                dr.path([("M", (sgn * (axis(y) - KW / 2), y)), ("L", (sgn * (axis(y) + KW / 2), y))], sw=1.3)
        dr.anchors["knee_panel"] = (-(axis(yk) + KW / 2) * 0.7 - 0.0, yk)
    # hem
    hs = spec.get("hem_style", "plain")
    hb = {"ribbed": 6.0, "elastic": 4.5, "drawcord": 4.5, "plain": 3.0, "curved": 3.0, "cuffed": 5.0}[hs]
    for sgn in (-1, 1):
        x_o = axis(yh - hb) + (LO + (KW - LO) * (hb / (yh - yk))) / 2 if yh > yk else out_h[0]
        x_i = max(0.9, axis(yh - hb) - (LO + (KW - LO) * (hb / (yh - yk))) / 2)
        yb2 = yh - hb
        if hs in ("ribbed", "elastic", "drawcord", "cuffed"):
            dr.line((sgn * x_i, yb2), (sgn * x_o, yb2), sw=1.4)
            if hs == "ribbed":
                a, b = sorted((x_i, x_o)); rib_hatch(dr, sgn * a if sgn > 0 else -b, sgn * b if sgn > 0 else -a, yb2 + 0.15, yh - 0.15, 1.0)
            if hs == "cuffed":
                dr.line((sgn * (x_i + 0.4), yh - 1.2), (sgn * (x_o - 0.4), yh - 1.2), dash=True, sw=0.9)
        else:
            dr.line((sgn * x_i, yb2), (sgn * x_o, yb2), dash=True, sw=1.2)
    dr.anchors["hem"] = (-(out_h[0] - 2), yh - hb / 2)
    dr.anchors["side_seam"] = (-(out_k[0]), yk - 6)
    marks = {}
    marks["waist_width"] = ("h", (-WW / 2, -1.8), (WW / 2, -1.8))
    marks["hip_width"] = ("h", (-HipW / 2, FR * 0.55), (HipW / 2, FR * 0.55))
    marks["front_rise"] = ("v", (-1.2, 0), (-1.2, FR), 0.22)
    marks["inseam"] = ("v", (in_k[0] - 1.8, FR), (in_h[0] - 1.8 + 0.0, yh)) if False else ("v", (in_k[0] + 1.6, FR), (in_k[0] + 1.6, yh))
    marks["thigh_width"] = ("h", (axis(yt) - TW / 2 if False else crotch[0] + 0.0, yt), (out_t[0], yt))
    marks["knee_width"] = ("h", (in_k[0], yk), (out_k[0], yk))
    marks["leg_opening"] = ("h", (in_h[0], yh + 2.4), (out_h[0], yh + 2.4))
    marks["waistband_height"] = ("v", (WW / 2 - 2.0, 0), (WW / 2 - 2.0, WBH))
    return marks


# ---------------------------------------------------------------- markers + callouts

def _arrow(dr: _Draw, p: Pt, q: Pt) -> str:
    x, y, x2, y2 = dr.X(p[0]), dr.Y(p[1]), dr.X(q[0]), dr.Y(q[1])
    ang = math.atan2(y2 - y, x2 - x)
    L, w = 7.0, 2.6
    return (f'<path d="M {x:.1f},{y:.1f} l {L * math.cos(ang) - w * math.sin(ang):.1f},{L * math.sin(ang) + w * math.cos(ang):.1f} '
            f'l 0,0 M {x:.1f},{y:.1f} l {L * math.cos(ang) + w * math.sin(ang):.1f},{L * math.sin(ang) - w * math.cos(ang):.1f}" '
            f'fill="none" stroke="{POM}" stroke-width="1.2" stroke-linecap="round"/>')


def add_markers(dr: _Draw, marks: Dict[str, tuple], dims_to_code: Dict[str, str]):
    for role, code in dims_to_code.items():
        m = marks.get(role)
        if not m:
            continue
        a, b = m[1], m[2]
        t = m[3] if len(m) > 3 else 0.5
        dr.parts.append(f'<path d="M {dr.P(a)} L {dr.P(b)}" fill="none" stroke="{POM}" stroke-width="1.1"/>')
        dr.parts.append(_arrow(dr, a, b)); dr.parts.append(_arrow(dr, b, a))
        cx, cy = dr.X(a[0]) + (dr.X(b[0]) - dr.X(a[0])) * t, dr.Y(a[1]) + (dr.Y(b[1]) - dr.Y(a[1])) * t
        label = str(code)[:3]
        w = 8 + 7 * len(label)
        dr.parts.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - 8:.1f}" width="{w:.1f}" height="16" rx="3" fill="{POM}"/>')
        dr.parts.append(f'<text x="{cx:.1f}" y="{cy + 4.2:.1f}" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
                        f'font-size="11" font-weight="700" fill="#fff">{_esc(label)}</text>')


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def add_callouts(dr: _Draw, callouts: List[dict], view: str, half_px: float, piece: int = 0) -> List[int]:
    """Numbered circles beside the silhouette with leader lines to the feature. Returns numbers drawn.
    A "p2:" prefix on an anchor sends it to the second garment of a set (piece 1); unprefixed anchors go to the first."""
    placed = []
    items = []
    for c in callouts:
        a = _norm_anchor(c.get("anchor", ""))
        second = a.startswith("p2:")
        if second != bool(piece):
            continue
        if second:
            a = a[3:]
        if ":" in a:
            v, a = a.split(":", 1)
            if v != view:
                continue
        pt = dr.anchors.get(a)
        if pt is None:
            continue
        items.append((c["number"], pt))
    items.sort(key=lambda t: dr.Y(t[1][1]))
    lastL = lastR = -1e9
    for num, pt in items:
        ax, ay = dr.X(pt[0]), dr.Y(pt[1])
        right = ax > dr.ox + 1.0
        cx = dr.ox + (half_px + 22) * (1 if right else -1)
        cy = ay
        last = lastR if right else lastL
        if cy - last < 26:
            cy = last + 26
        if right:
            lastR = cy
        else:
            lastL = cy
        dr.call.append(f'<path d="M {ax:.1f},{ay:.1f} L {cx - (11 if right else -11) * 1:.1f},{cy:.1f}" fill="none" stroke="{INK}" stroke-width="1"/>')
        dr.call.append(f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="2.2" fill="{INK}"/>')
        dr.call.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10.5" fill="#fff" stroke="{INK}" stroke-width="1.4"/>')
        dr.call.append(f'<text x="{cx:.1f}" y="{cy + 4.3:.1f}" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
                       f'font-size="12" font-weight="700" fill="{INK}">{num}</text>')
        placed.append(num)
    return placed


# ---------------------------------------------------------------- public API

def _render_piece(pack: dict, spec: dict, piece: int) -> Optional[dict]:
    template = spec.get("template")
    if template not in ("top", "bottom"):
        return None
    d, warnings = resolve_dims(pack, template, spec)
    draw, extent = (draw_top, _top_extent) if template == "top" else (draw_bottom, _bottom_extent)
    half, ytop, ybot = extent(d, spec)
    s = min((VW - 2 * PAD_X) / (2 * half), (680 - PAD_TOP - PAD_BOT) / (ybot - ytop))
    vh = int((ybot - ytop) * s + PAD_TOP + PAD_BOT)
    ox, oy = VW / 2, PAD_TOP - ytop * s
    code_for = {x.get("role"): str(x.get("pom_code", "")).strip().upper() for x in spec.get("dims", [])}
    out: Dict[str, object] = {"warnings": warnings, "label": "Top" if template == "top" else "Bottom"}
    drawn = set()
    for view in ("front", "back"):
        for markers in (False, True):
            dr = _Draw(s, ox, oy)
            marks = draw(dr, d, spec, view)
            if markers:
                add_markers(dr, marks, {r: c for r, c in code_for.items() if r in marks})
            nums = add_callouts(dr, pack.get("callouts", []), view, half * s + 14, piece)
            drawn.update(nums)
            body = "".join(dr.back) + "".join(dr.parts) + "".join(dr.call)
            out[view + ("_pom" if markers else "")] = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VW} {vh}" width="{VW}" height="{vh}">'
                f'<rect width="{VW}" height="{vh}" fill="#fff"/>{body}</svg>')
    out["drawn"] = sorted(drawn)
    return out


def render_all(pack: dict) -> List[dict]:
    """One drawn set (front/back, with and without markers) per garment: the main flat_spec and, for two-piece sets, flat_spec_2."""
    pieces = []
    for i, key in enumerate(("flat_spec", "flat_spec_2")):
        r = _render_piece(pack, pack.get(key) or {}, i)
        if r:
            pieces.append(r)
    if len(pieces) == 2 and pieces[0]["label"] == pieces[1]["label"]:
        pieces[0]["label"], pieces[1]["label"] = "Piece 1", "Piece 2"
    return pieces


def render_flats(pack: dict) -> Optional[dict]:
    """The main garment's {front, back, front_pom, back_pom, drawn, warnings}; None if this pack uses stored drawings.
    drawn and warnings cover every garment in the set."""
    pieces = render_all(pack)
    if not pieces or not (pack.get("flat_spec") or {}).get("template") in ("top", "bottom"):
        return None
    out = dict(pieces[0])
    out["drawn"] = sorted({n for p in pieces for n in p["drawn"]})
    out["warnings"] = [w for p in pieces for w in p["warnings"]]
    return out
