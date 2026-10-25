"""Checks, diffs and edits that work on the plain pack data (no model calls)."""

import re
from typing import Any, Dict, List

from .flats import render_flats

SECTION_LABELS = {
    "style_name": "Cover", "style_number": "Cover", "season": "Cover", "category": "Cover", "gender_fit": "Cover",
    "summary": "Cover", "key_design_details": "Cover", "fit_notes": "Cover", "country_of_origin": "Cover",
    "front_flat_svg": "Flats", "back_flat_svg": "Flats", "callouts": "Flats", "flat_spec": "Flats",
    "bill_of_materials": "Bill of materials", "colorways": "Colourways",
    "units": "Measurements", "base_size": "Measurements", "sizes": "Measurements", "measurements": "Measurements",
    "construction": "Construction", "artwork": "Artwork", "labels_and_trims": "Labels & trims",
    "wash_and_finishing": "Finishing", "care_instructions": "Finishing", "packaging": "Packing", "packing_details": "Packing",
    "quality_requirements": "Quality", "sampling_requirements": "Sampling", "testing_requirements": "Testing",
    "compliance_notes": "Compliance", "assumptions": "Review", "questions_for_designer": "Review",
}
PLACEHOLDER = re.compile(r"\[[^\]]{2,}\]|placeholder|to be confirmed|\bTBC\b|\bTBD\b|replace with", re.I)
SVG_FIELDS = ("front_flat_svg", "back_flat_svg")


def _num(v: Any):
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def _walk(node: Any, path: str = ""):
    """Yield (path, string) for every string leaf."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}.{i}")
    elif isinstance(node, str):
        yield path, node


def check_pack(p: Dict[str, Any]) -> List[Dict[str, str]]:
    """Deterministic sanity checks a factory would otherwise find for you."""
    out: List[Dict[str, str]] = []

    def add(level, section, text, path=""):
        out.append({"level": level, "section": section, "text": text, "path": path})

    sizes = p.get("sizes", [])
    if p.get("base_size") not in sizes:
        add("warn", "Measurements", f"Base size {p.get('base_size')!r} is not one of the sizes {sizes}.")

    for mi, m in enumerate(p.get("measurements", [])):
        vals = m.get("values", [])
        tag = f"POM {m.get('code')} ({m.get('description', '')[:40]})"
        if len(vals) != len(sizes):
            add("warn", "Measurements", f"{tag}: {len(vals)} values for {len(sizes)} sizes.", f"measurements.{mi}.description")
            continue
        nums = [_num(v) for v in vals]
        if None in nums:
            add("warn", "Measurements", f"{tag}: a value is not a number.", f"measurements.{mi}.values.0")
        elif any(b < a for a, b in zip(nums, nums[1:])):
            add("warn", "Measurements", f"{tag}: gets smaller as sizes go up ({', '.join(map(str, vals))}).", f"measurements.{mi}.values.0")
        if not str(m.get("tolerance", "")).strip():
            add("warn", "Measurements", f"{tag}: no tolerance.", f"measurements.{mi}.tolerance")

    for bi, b in enumerate(p.get("bill_of_materials", [])):
        if not str(b.get("pantone", "")).strip():
            add("warn", "Materials", f"{b.get('component')}: no Pantone/colour reference.", f"bill_of_materials.{bi}.pantone")

    flats = render_flats(p)
    if flats:
        drawn_set = {str(n) for n in flats["drawn"]}
        for w in flats["warnings"]:
            add("warn", "Flats", w)
        if not (p.get("flat_spec") or {}).get("dims"):
            add("warn", "Flats", "No flat dimensions are mapped to the measurement table, so the drawings use default proportions.")
        for c in p.get("callouts", []):
            if str(c.get("number")) not in drawn_set:
                add("warn", "Flats", f"Callout {c.get('number')} ({c.get('label')}) isn't attached to a drawn feature.", f"callouts.{p['callouts'].index(c)}.anchor")
    else:
        drawn_set = set(re.findall(r">\s*(\d+)\s*<", p.get("front_flat_svg", "") + p.get("back_flat_svg", "")))
        for c in p.get("callouts", []):
            if str(c.get("number")) not in drawn_set:
                add("warn", "Flats", f"Callout {c.get('number')} ({c.get('label')}) is listed but not drawn on either flat.")
    known = {str(c.get("number")) for c in p.get("callouts", [])}
    for si, s in enumerate(p.get("construction", [])):
        for ref in re.findall(r"\d+", str(s.get("callout_ref", ""))):
            if ref not in known:
                add("warn", "Construction", f"{s.get('area')}: refers to callout {ref}, which doesn't exist.", f"construction.{si}.callout_ref")
        if not str(s.get("seam_allowance", "")).strip():
            add("warn", "Construction", f"{s.get('area')}: no seam allowance.", f"construction.{si}.seam_allowance")

    if not flats:
        for key in SVG_FIELDS:
            if not p.get(key, "").lstrip().startswith("<svg"):
                add("warn", "Flats", f"{key.split('_')[0].title()} flat is missing or not valid SVG.")

    for key in ("sampling_requirements", "testing_requirements", "compliance_notes", "fit_notes"):
        if not p.get(key):
            add("warn", SECTION_LABELS.get(key, "Pack"), f"{key.replace('_', ' ').capitalize()} is empty.")

    hits = [(path, s) for path, s in _walk({k: v for k, v in p.items() if k not in SVG_FIELDS + ("assumptions", "questions_for_designer")})
            if PLACEHOLDER.search(s)]
    for path, s in hits[:15]:
        top = path.split(".")[0]
        add("todo", SECTION_LABELS.get(top, "Pack"), f"“{s[:90]}{'…' if len(s) > 90 else ''}”", path)
    if len(hits) > 15:
        add("todo", "Pack", f"…and {len(hits) - 15} more placeholders.")
    return out


# ---------- diff ----------

def _flatten(node: Any) -> Dict[str, str]:
    return dict(_walk(node))


def diff_packs(a: Dict[str, Any], b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Field-level changes grouped by section, from version a to version b."""
    fa, fb = _flatten(a), _flatten(b)
    groups: Dict[str, List[Dict[str, str]]] = {}
    for path in sorted(set(fa) | set(fb), key=lambda s: [int(x) if x.isdigit() else x for x in s.split(".")]):
        old, new = fa.get(path), fb.get(path)
        if old == new:
            continue
        top = path.split(".")[0]
        section = SECTION_LABELS.get(top, "Other")
        if top in SVG_FIELDS:
            old, new = "(drawing)", "(redrawn)"
        groups.setdefault(section, [])
        if any(g["path"] == top for g in groups[section]) and top in SVG_FIELDS:
            continue
        groups[section].append({"path": path if top not in SVG_FIELDS else top, "old": old, "new": new})
    return [{"section": s, "changes": c} for s, c in groups.items()]


def summarise(groups: List[Dict[str, Any]]) -> str:
    total = sum(len(g["changes"]) for g in groups)
    if not total:
        return "no changes"
    parts = ", ".join(f"{g['section']} {len(g['changes'])}" for g in groups[:4])
    more = f" +{len(groups) - 4} more" if len(groups) > 4 else ""
    return f"{total} changes: {parts}{more}"


# ---------- manual edits ----------

def apply_edits(pack: Dict[str, Any], edits: Dict[str, str]) -> int:
    """Set string values at dotted paths like 'measurements.3.values.1'. Returns how many changed."""
    changed = 0
    for path, value in edits.items():
        keys = path.split(".")
        if keys[0] in SVG_FIELDS:
            raise ValueError("Flat drawings can't be edited by hand; use Revise.")
        node = pack
        try:
            for k in keys[:-1]:
                node = node[int(k)] if isinstance(node, list) else node[k]
            last = keys[-1]
            if isinstance(node, list):
                idx = int(last)
                if node[idx] != value:
                    node[idx] = value
                    changed += 1
            else:
                if last not in node or not isinstance(node[last], str):
                    raise KeyError(path)
                if node[last] != value:
                    node[last] = value
                    changed += 1
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise ValueError(f"Cannot edit {path!r}") from e
    return changed
