"""Local web app: upload reference images + brief -> printable tech pack."""

import base64
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .generator import GenerationError, ReferenceImage, generate, load_image, revise
from .schema import TechPack

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TECHPACK_DATA", BASE_DIR.parent / "packs"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API limit per image
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

app = FastAPI(title="Tech Pack Generator")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def svg_data_uri(svg: str) -> str:
    # Rendered as <img> so any script in model output can't run.
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


templates.env.filters["svg_uri"] = svg_data_uri


def grade_steps(values) -> str:
    """Grade increments between consecutive sizes, e.g. '+3.0 / +3.0 / +3.0'."""
    nums = []
    for v in values:
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        if not m:
            return "–"
        nums.append(float(m.group()))
    if len(nums) < 2:
        return "–"
    steps = [round(b - a, 2) for a, b in zip(nums, nums[1:])]
    if len(set(steps)) == 1:
        return f"{steps[0]:+g} per size"
    return " / ".join(f"{d:+g}" for d in steps)


templates.env.filters["grade"] = grade_steps


def _pack_dir(pack_id: str) -> Path:
    if not SAFE_NAME.match(pack_id):
        raise HTTPException(404)
    path = DATA_DIR / pack_id
    if not path.is_dir():
        raise HTTPException(404)
    return path


def _versions(pack_dir: Path) -> List[int]:
    return sorted(int(p.stem[1:]) for p in pack_dir.glob("v*.json") if p.stem[1:].isdigit())


def _images(pack_dir: Path) -> List[ReferenceImage]:
    return [img for p in sorted((pack_dir / "images").iterdir()) if (img := load_image(str(p)))]


def _save_version(pack_dir: Path, pack: TechPack, note: str) -> int:
    version = (_versions(pack_dir) or [0])[-1] + 1
    (pack_dir / f"v{version}.json").write_text(json.dumps(pack.model_dump(), indent=2))
    meta_path = pack_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.setdefault("history", []).append({"version": version, "note": note, "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    meta["style_name"] = pack.style_name
    meta_path.write_text(json.dumps(meta, indent=2))
    return version


def _list_packs() -> list:
    packs = []
    for d in sorted(DATA_DIR.iterdir(), reverse=True):
        meta = d / "meta.json"
        if d.is_dir() and meta.exists() and _versions(d):
            m = json.loads(meta.read_text())
            packs.append({"id": d.name, "name": m.get("style_name", d.name), "created": m.get("created", ""), "versions": len(_versions(d))})
    return packs


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"packs": _list_packs(), "error": None})


@app.post("/generate")
async def create_pack(
    request: Request,
    images: List[UploadFile] = File(...),
    style_name: str = Form(""),
    category: str = Form(""),
    season: str = Form(""),
    fit: str = Form(""),
    fabric: str = Form(""),
    colors: str = Form(""),
    brand: str = Form(""),
    factory: str = Form(""),
    order_qty: str = Form(""),
    delivery: str = Form(""),
    sample_stage: str = Form(""),
    fit_reference: str = Form(""),
    sizes: str = Form("XS, S, M, L, XL"),
    units: str = Form("cm"),
    graphics: str = Form(""),
    trims: str = Form(""),
    target_price: str = Form(""),
    notes: str = Form(""),
):
    refs: List[ReferenceImage] = []
    for upload in images:
        if not upload.filename:
            continue
        data = await upload.read()
        if upload.content_type not in ALLOWED_TYPES:
            return _form_error(request, f"{upload.filename}: use JPG, PNG, WEBP or GIF.")
        if len(data) > MAX_IMAGE_BYTES:
            return _form_error(request, f"{upload.filename} is over 5 MB. Export a smaller version.")
        refs.append(ReferenceImage(data, upload.content_type, upload.filename))
    if not refs:
        return _form_error(request, "Upload at least one reference image.")

    brief = {
        "brand": brand, "factory_or_supplier": factory, "order_quantity": order_qty,
        "target_delivery": delivery, "sample_stage": sample_stage, "fit_reference_or_model_size": fit_reference,
        "style_name": style_name, "category": category, "season": season, "fit": fit,
        "fabric": fabric, "colours": colors, "sizes": sizes, "units": units,
        "graphics_and_artwork": graphics, "trims_and_hardware": trims,
        "target_cost": target_price, "other_notes": notes,
    }

    try:
        pack = await _in_thread(generate, refs, brief)
    except GenerationError as e:
        return _form_error(request, str(e))

    slug = re.sub(r"[^a-z0-9]+", "-", (pack.style_name or "pack").lower()).strip("-")[:40] or "pack"
    pack_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"
    pack_dir = DATA_DIR / pack_id
    (pack_dir / "images").mkdir(parents=True)
    for i, ref in enumerate(refs, 1):
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}[ref.media_type]
        (pack_dir / "images" / f"ref{i:02d}{ext}").write_bytes(ref.data)
    (pack_dir / "meta.json").write_text(json.dumps({"brief": brief, "created": datetime.now().strftime("%Y-%m-%d %H:%M")}, indent=2))
    _save_version(pack_dir, pack, "Initial generation")
    return RedirectResponse(f"/packs/{pack_id}", status_code=303)


@app.get("/packs/{pack_id}", response_class=HTMLResponse)
def view_pack(request: Request, pack_id: str, v: int = 0, error: str = ""):
    pack_dir = _pack_dir(pack_id)
    versions = _versions(pack_dir)
    version = v if v in versions else versions[-1]
    pack = TechPack.model_validate_json((pack_dir / f"v{version}.json").read_text())
    meta = json.loads((pack_dir / "meta.json").read_text())
    image_names = sorted(p.name for p in (pack_dir / "images").iterdir())
    return templates.TemplateResponse(
        request,
        "pack.html",
        {
            "pack": pack, "pack_id": pack_id, "version": version, "versions": versions,
            "latest": versions[-1], "meta": meta, "images": image_names, "error": error,
        },
    )


@app.post("/packs/{pack_id}/revise")
async def revise_pack(pack_id: str, feedback: str = Form(...), v: int = Form(0)):
    pack_dir = _pack_dir(pack_id)
    versions = _versions(pack_dir)
    base = v if v in versions else versions[-1]
    previous = TechPack.model_validate_json((pack_dir / f"v{base}.json").read_text())
    try:
        pack = await _in_thread(revise, previous, _images(pack_dir), feedback)
    except GenerationError as e:
        return RedirectResponse(f"/packs/{pack_id}?v={base}&error={quote(str(e))}", status_code=303)
    new_version = _save_version(pack_dir, pack, f"Revised from v{base}: {feedback[:120]}")
    return RedirectResponse(f"/packs/{pack_id}?v={new_version}", status_code=303)


@app.get("/packs/{pack_id}/json")
def pack_json(pack_id: str, v: int = 0):
    pack_dir = _pack_dir(pack_id)
    versions = _versions(pack_dir)
    version = v if v in versions else versions[-1]
    return JSONResponse(json.loads((pack_dir / f"v{version}.json").read_text()))


@app.get("/packs/{pack_id}/xlsx")
def pack_xlsx(pack_id: str, v: int = 0):
    """Editable export: factories dislike locked PDFs."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    pack_dir = _pack_dir(pack_id)
    versions = _versions(pack_dir)
    version = v if v in versions else versions[-1]
    pack = TechPack.model_validate_json((pack_dir / f"v{version}.json").read_text())
    meta = json.loads((pack_dir / "meta.json").read_text())
    brief = meta.get("brief", {})

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="DDDDDD")

    def sheet(title, header, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = head_fill
        for r in rows:
            ws.append([str(c) for c in r])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(60, max(12, max(len(str(c.value or "")) for c in col) + 2))
        ws.freeze_panes = "A2"

    cover = [
        ("Style name", pack.style_name), ("Style number", pack.style_number), ("Version", f"v{version}"),
        ("Brand", brief.get("brand", "")), ("Factory / supplier", brief.get("factory_or_supplier", "")),
        ("Season", pack.season), ("Category", pack.category), ("Fit", pack.gender_fit),
        ("Sample stage", brief.get("sample_stage", "")), ("Order quantity", brief.get("order_quantity", "")),
        ("Target delivery", brief.get("target_delivery", "")), ("Target cost", brief.get("target_cost", "")),
        ("Units", pack.units), ("Sizes", ", ".join(pack.sizes)), ("Base size", pack.base_size),
        ("Country of origin", pack.country_of_origin), ("Summary", pack.summary),
    ]
    sheet("Cover", ["Field", "Value"], cover, first=True)
    sheet("BOM", ["Component", "Material", "Composition", "Weight/size", "Colour", "Pantone", "Placement", "Qty", "Notes"],
          [(b.component, b.material, b.composition, b.weight_or_size, b.color, b.pantone, b.placement, b.quantity, b.notes) for b in pack.bill_of_materials])
    sheet("Colourways", ["Colourway", "Pantone", "Applies to"], [(c.name, c.pantone, c.applies_to) for c in pack.colorways])
    sheet("Measurements", ["Code", "Point of measure", "Tol +/-", "Grade"] + pack.sizes,
          [(m.code, m.description, m.tolerance, grade_steps(m.values), *m.values) for m in pack.measurements])
    sheet("Construction", ["Area", "Operation", "Stitch", "Seam", "SPI", "Seam allowance", "Callout", "Notes"],
          [(c.area, c.operation, c.stitch_type, c.seam_type, c.spi, c.seam_allowance, c.callout_ref, c.notes) for c in pack.construction])
    sheet("Artwork", ["Artwork", "Technique", "Size", "Placement", "Colours", "Notes"],
          [(a.name, a.technique, a.size, a.placement, a.colors, a.notes) for a in pack.artwork])
    sheet("Labels & trims", ["Item", "Spec", "Size", "Content", "Placement"],
          [(l.item, l.spec, l.size, l.content, l.placement) for l in pack.labels_and_trims])
    sheet("Testing", ["Test", "Standard", "Requirement"], [(t.test, t.standard, t.requirement) for t in pack.testing_requirements])
    pk = pack.packing_details
    sheet("Packing", ["Item", "Specification"], [
        ("Folding", pk.folding), ("Polybag", pk.polybag), ("Hangtag / stickers", pk.hangtag_and_stickers),
        ("Units per carton", pk.units_per_carton), ("Packing ratio", pk.packing_ratio), ("Carton marking", pk.carton_marking),
    ])
    sheet("Callouts", ["#", "Detail", "Description"], [(c.number, c.label, c.description) for c in pack.callouts])
    sheet("Revision log", ["Version", "Date", "Note"], [(h["version"], h["at"], h["note"]) for h in meta.get("history", [])])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"{pack_id}-v{version}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/packs/{pack_id}/images/{name}")
def pack_image(pack_id: str, name: str):
    if not SAFE_NAME.match(name):
        raise HTTPException(404)
    path = _pack_dir(pack_id) / "images" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


def _form_error(request: Request, message: str):
    return templates.TemplateResponse(request, "index.html", {"packs": _list_packs(), "error": message}, status_code=400)


async def _in_thread(fn, *args):
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(fn, *args)
