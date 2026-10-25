"""Selvedge: local web app. Upload reference images + brief -> factory-ready tech pack.

Generation takes minutes, so it runs as a background job. The pack page shows
progress and picks up the result, and you can close the tab and come back.
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .export import build_xlsx, grade_steps
from pydantic import ValidationError

from .generator import GenerationError, ReferenceImage, generate, load_image, revise
from .insights import apply_edits, check_pack, diff_packs, summarise
from .schema import TechPack

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TECHPACK_DATA", BASE_DIR.parent / "packs"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # API limit per image
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
BUSY = ("generating", "revising")

app = FastAPI(title="Selvedge")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

JOBS: dict = {}  # pack_id -> running thread
_LOCK = threading.RLock()


def svg_data_uri(svg: str) -> str:
    # Rendered as <img> so any script in model output can't run.
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


templates.env.filters["svg_uri"] = svg_data_uri
templates.env.filters["grade"] = grade_steps
# Models sometimes append notes like "(placeholder, replace with...)" to short identifiers.
templates.env.filters["short"] = lambda s: re.split(r"\s*[(\[]", str(s or ""))[0].strip()


# ---------- storage helpers ----------

def _pack_dir(pack_id: str) -> Path:
    if not SAFE_NAME.match(pack_id):
        raise HTTPException(404)
    path = DATA_DIR / pack_id
    if not path.is_dir():
        raise HTTPException(404)
    return path


def _versions(pack_dir: Path) -> List[int]:
    return sorted(int(p.stem[1:]) for p in pack_dir.glob("v*.json") if p.stem[1:].isdigit())


def _image_names(pack_dir: Path) -> List[str]:
    folder = pack_dir / "images"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _images(pack_dir: Path) -> List[ReferenceImage]:
    return [img for p in sorted((pack_dir / "images").iterdir()) if (img := load_image(str(p)))]


def _read_meta(pack_dir: Path) -> dict:
    with _LOCK:
        return json.loads((pack_dir / "meta.json").read_text())


def _update_meta(pack_dir: Path, **changes) -> dict:
    """Merge changes into meta.json. A value of None removes the key."""
    with _LOCK:
        meta = _read_meta(pack_dir)
        for k, v in changes.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        (pack_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return meta


def _save_version(pack_dir: Path, pack: TechPack, note: str) -> int:
    with _LOCK:
        existing = _versions(pack_dir)
        version = (existing or [0])[-1] + 1
        new = pack.model_dump()
        entry = {"version": version, "note": note, "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        if existing:
            prev = json.loads((pack_dir / f"v{existing[-1]}.json").read_text())
            entry["summary"] = summarise(diff_packs(prev, new))
        (pack_dir / f"v{version}.json").write_text(json.dumps(new, indent=2))
        meta = _read_meta(pack_dir)
        meta.setdefault("history", []).append(entry)
        meta["style_name"] = pack.style_name
        (pack_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return version


def _running(pack_dir: Path) -> bool:
    """True only while a job is genuinely in progress (status is saved before the thread exits)."""
    return pack_dir.name in JOBS and _read_meta(pack_dir).get("status") in BUSY


def _meta_with_status(pack_dir: Path) -> dict:
    """Meta, with jobs that died (e.g. server restart) marked as interrupted."""
    meta = _read_meta(pack_dir)
    if meta.get("status") in BUSY and pack_dir.name not in JOBS:
        had_versions = bool(_versions(pack_dir))
        meta = _update_meta(
            pack_dir,
            status="ready" if had_versions else "failed",
            error="This job was interrupted (the server restarted). Please try again.",
        )
    return meta


# ---------- background jobs ----------

def _start_job(pack_dir: Path, kind: str, note: str, fn, *args) -> None:
    pack_id = pack_dir.name
    _update_meta(pack_dir, status=kind, error=None, started=time.time(), job_note=note)

    def run():
        try:
            pack = fn(*args)
            _save_version(pack_dir, pack, note)
            _update_meta(pack_dir, status="ready", error=None, started=None, job_note=None)
        except GenerationError as e:
            _fail(pack_dir, str(e))
        except Exception as e:  # keep the job from dying silently
            _fail(pack_dir, f"Unexpected error: {e}")
        finally:
            JOBS.pop(pack_id, None)

    thread = threading.Thread(target=run, daemon=True, name=f"job-{pack_id}")
    JOBS[pack_id] = thread
    thread.start()


def _fail(pack_dir: Path, message: str) -> None:
    status = "ready" if _versions(pack_dir) else "failed"
    _update_meta(pack_dir, status=status, error=message, started=None, job_note=None)


# ---------- pages ----------

def _list_packs() -> list:
    packs = []
    for d in sorted(DATA_DIR.iterdir(), reverse=True):
        if not (d.is_dir() and (d / "meta.json").exists()):
            continue
        m = _meta_with_status(d)
        names = _image_names(d)
        packs.append({
            "id": d.name,
            "name": m.get("style_name") or m.get("brief", {}).get("style_name") or "Untitled style",
            "category": m.get("brief", {}).get("category", ""),
            "created": m.get("created", ""),
            "versions": len(_versions(d)),
            "status": m.get("status", "ready"),
            "thumb": names[0] if names else None,
        })
    return packs


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"packs": _list_packs(), "error": None, "form": {}})


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
    speed: str = Form("thorough"),
):
    brief = {
        "brand": brand, "factory_or_supplier": factory, "order_quantity": order_qty,
        "target_delivery": delivery, "sample_stage": sample_stage, "fit_reference_or_model_size": fit_reference,
        "style_name": style_name, "category": category, "season": season, "fit": fit,
        "fabric": fabric, "colours": colors, "sizes": sizes, "units": units,
        "graphics_and_artwork": graphics, "trims_and_hardware": trims,
        "target_cost": target_price, "other_notes": notes,
    }

    refs: List[ReferenceImage] = []
    for upload in images:
        if not upload.filename:
            continue
        data = await upload.read()
        if upload.content_type not in ALLOWED_TYPES:
            return _form_error(request, f"{upload.filename}: use JPG, PNG, WEBP or GIF.", brief)
        if len(data) > MAX_IMAGE_BYTES:
            return _form_error(request, f"{upload.filename} is over 5 MB. Export a smaller version.", brief)
        refs.append(ReferenceImage(data, upload.content_type, upload.filename))
    if not refs:
        return _form_error(request, "Add at least one reference image.", brief)

    effort = "medium" if speed == "fast" else "high"
    slug = re.sub(r"[^a-z0-9]+", "-", (style_name or category or "pack").lower()).strip("-")[:40] or "pack"
    pack_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"
    pack_dir = DATA_DIR / pack_id
    (pack_dir / "images").mkdir(parents=True)
    for i, ref in enumerate(refs, 1):
        (pack_dir / "images" / f"ref{i:02d}{EXT[ref.media_type]}").write_bytes(ref.data)
    (pack_dir / "meta.json").write_text(json.dumps({
        "brief": brief, "style_name": style_name, "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "status": "generating",
        "effort": effort,
    }, indent=2))
    _start_job(pack_dir, "generating", "Initial generation", generate, refs, brief, effort)
    return RedirectResponse(f"/packs/{pack_id}", status_code=303)


@app.get("/packs/{pack_id}", response_class=HTMLResponse)
def view_pack(request: Request, pack_id: str, v: int = 0):
    pack_dir = _pack_dir(pack_id)
    meta = _meta_with_status(pack_dir)
    versions = _versions(pack_dir)
    ctx = {"pack_id": pack_id, "meta": meta, "images": _image_names(pack_dir), "status": meta.get("status", "ready"), "error": meta.get("error")}
    if not versions:
        return templates.TemplateResponse(request, "pending.html", ctx)
    version = v if v in versions else versions[-1]
    pack = TechPack.model_validate_json((pack_dir / f"v{version}.json").read_text())
    checks = check_pack(pack.model_dump())
    ctx.update({
        "pack": pack, "version": version, "versions": versions, "latest": versions[-1], "brief": meta.get("brief", {}),
        "checks": checks, "open_checks": len(checks), "has_browser": _browser() is not None,
    })
    return templates.TemplateResponse(request, "pack.html", ctx)


@app.get("/packs/{pack_id}/status")
def pack_status(pack_id: str):
    pack_dir = _pack_dir(pack_id)
    meta = _meta_with_status(pack_dir)
    return {
        "status": meta.get("status", "ready"),
        "error": meta.get("error"),
        "started": meta.get("started"),
        "versions": len(_versions(pack_dir)),
    }


@app.post("/packs/{pack_id}/revise")
async def revise_pack(pack_id: str, feedback: str = Form(...), v: int = Form(0)):
    pack_dir = _pack_dir(pack_id)
    if _running(pack_dir):
        return RedirectResponse(f"/packs/{pack_id}", status_code=303)
    versions = _versions(pack_dir)
    if not versions:
        raise HTTPException(409, "Nothing to revise yet.")
    base = v if v in versions else versions[-1]
    previous = TechPack.model_validate_json((pack_dir / f"v{base}.json").read_text())
    effort = _read_meta(pack_dir).get("effort")
    _start_job(pack_dir, "revising", f"Revised from v{base}: {feedback[:120]}", revise, previous, _images(pack_dir), feedback, effort)
    return RedirectResponse(f"/packs/{pack_id}", status_code=303)


@app.post("/packs/{pack_id}/retry")
def retry_pack(pack_id: str):
    pack_dir = _pack_dir(pack_id)
    if not _running(pack_dir) and not _versions(pack_dir):
        meta = _read_meta(pack_dir)
        _start_job(pack_dir, "generating", "Initial generation", generate, _images(pack_dir), meta.get("brief", {}), meta.get("effort"))
    return RedirectResponse(f"/packs/{pack_id}", status_code=303)


@app.post("/packs/{pack_id}/dismiss")
def dismiss_error(pack_id: str):
    _update_meta(_pack_dir(pack_id), error=None)
    return RedirectResponse(f"/packs/{pack_id}", status_code=303)


@app.post("/packs/{pack_id}/delete")
def delete_pack(pack_id: str):
    pack_dir = _pack_dir(pack_id)
    if _running(pack_dir):
        raise HTTPException(409, "This pack is still being generated.")
    shutil.rmtree(pack_dir)
    return RedirectResponse("/", status_code=303)


def _version_of(pack_dir: Path, v: int) -> int:
    versions = _versions(pack_dir)
    if not versions:
        raise HTTPException(404)
    return v if v in versions else versions[-1]


@app.get("/packs/{pack_id}/json")
def pack_json(pack_id: str, v: int = 0):
    pack_dir = _pack_dir(pack_id)
    version = _version_of(pack_dir, v)
    return JSONResponse(json.loads((pack_dir / f"v{version}.json").read_text()))


@app.get("/packs/{pack_id}/xlsx")
def pack_xlsx(pack_id: str, v: int = 0):
    pack_dir = _pack_dir(pack_id)
    version = _version_of(pack_dir, v)
    pack = TechPack.model_validate_json((pack_dir / f"v{version}.json").read_text())
    data = build_xlsx(pack, _read_meta(pack_dir), version)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{pack_id}-v{version}.xlsx"'},
    )


@app.get("/packs/{pack_id}/images/{name}")
def pack_image(pack_id: str, name: str):
    if not SAFE_NAME.match(name):
        raise HTTPException(404)
    path = _pack_dir(pack_id) / "images" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


# ---------- edit, compare, duplicate ----------

@app.post("/packs/{pack_id}/edit")
async def edit_pack(pack_id: str, request: Request):
    """Save hand edits as a new version (no model call)."""
    pack_dir = _pack_dir(pack_id)
    if _running(pack_dir):
        return JSONResponse({"error": "A job is running on this pack. Wait for it to finish."}, status_code=409)
    body = await request.json()
    versions = _versions(pack_dir)
    base = body.get("v") if body.get("v") in versions else versions[-1]
    edits = body.get("edits")
    if not isinstance(edits, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in edits.items()):
        return JSONResponse({"error": "Bad edit request."}, status_code=400)
    data = json.loads((pack_dir / f"v{base}.json").read_text())
    try:
        changed = apply_edits(data, edits)
        pack = TechPack.model_validate(data)
    except (ValueError, ValidationError) as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)
    if not changed:
        return {"version": base, "changed": 0}
    version = _save_version(pack_dir, pack, f"Manual edit of v{base}: {changed} field{'s' if changed != 1 else ''}")
    return {"version": version, "changed": changed}


@app.get("/packs/{pack_id}/compare", response_class=HTMLResponse)
def compare(request: Request, pack_id: str, a: int = 0, b: int = 0):
    pack_dir = _pack_dir(pack_id)
    versions = _versions(pack_dir)
    if not versions:
        raise HTTPException(404)
    b = b if b in versions else versions[-1]
    a = a if a in versions else (versions[-2] if len(versions) > 1 else versions[-1])
    groups = diff_packs(json.loads((pack_dir / f"v{a}.json").read_text()), json.loads((pack_dir / f"v{b}.json").read_text())) if a != b else []
    meta = _read_meta(pack_dir)
    return templates.TemplateResponse(request, "compare.html", {
        "pack_id": pack_id, "meta": meta, "versions": versions, "a": a, "b": b, "groups": groups,
        "summary": summarise(groups), "name": meta.get("style_name") or "Tech pack",
    })


@app.post("/packs/{pack_id}/duplicate")
def duplicate_pack(pack_id: str):
    """Copy the latest version into a new pack, e.g. to make a colourway or variant."""
    src = _pack_dir(pack_id)
    versions = _versions(src)
    if not versions:
        raise HTTPException(409, "Nothing to duplicate yet.")
    meta = _read_meta(src)
    latest = versions[-1]
    data = json.loads((src / f"v{latest}.json").read_text())
    name = data.get("style_name") or "Untitled style"
    data["style_name"] = f"{name} (copy)"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "pack"
    new_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slug}-copy"
    dst = DATA_DIR / new_id
    shutil.copytree(src / "images", dst / "images")
    (dst / "v1.json").write_text(json.dumps(data, indent=2))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_meta = {
        "brief": meta.get("brief", {}), "style_name": data["style_name"], "created": now, "status": "ready",
        "history": [{"version": 1, "note": f"Duplicated from {name} v{latest}", "at": now}],
    }
    if meta.get("effort"):
        new_meta["effort"] = meta["effort"]
    (dst / "meta.json").write_text(json.dumps(new_meta, indent=2))
    return RedirectResponse(f"/packs/{new_id}", status_code=303)


# ---------- real PDF + factory bundle ----------

def _browser() -> Optional[str]:
    """A Chromium-based browser used to print the pack to PDF."""
    candidates = [os.environ.get("TECHPACK_BROWSER")]
    candidates += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    candidates += [shutil.which(n) for n in ("msedge", "google-chrome", "chromium", "chrome")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def _render_pdf(url: str) -> bytes:
    browser = _browser()
    if not browser:
        raise HTTPException(501, "No Edge or Chrome found to make the PDF. Use Print instead, or set TECHPACK_BROWSER.")
    with tempfile.TemporaryDirectory(prefix="selvedge-pdf-") as tmp:
        out = os.path.join(tmp, "pack.pdf")
        try:
            subprocess.run(
                [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                 f"--user-data-dir={os.path.join(tmp, 'profile')}", "--virtual-time-budget=6000",
                 f"--print-to-pdf={out}", url],
                capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Making the PDF took too long.")
        if not os.path.exists(out):
            raise HTTPException(500, "The browser did not produce a PDF.")
        with open(out, "rb") as f:
            return f.read()


def _file_stem(pack_dir: Path, version: int) -> str:
    data = json.loads((pack_dir / f"v{version}.json").read_text())
    slug = re.sub(r"[^A-Za-z0-9]+", "-", data.get("style_name") or "tech-pack").strip("-")[:40] or "tech-pack"
    return f"{slug}-v{version}"


@app.get("/packs/{pack_id}/pdf")
def pack_pdf(request: Request, pack_id: str, v: int = 0):
    pack_dir = _pack_dir(pack_id)
    version = _version_of(pack_dir, v)
    pdf = _render_pdf(f"http://127.0.0.1:{request.url.port or 80}/packs/{pack_id}?v={version}")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_file_stem(pack_dir, version)}.pdf"'})


@app.get("/packs/{pack_id}/bundle")
def pack_bundle(request: Request, pack_id: str, v: int = 0):
    """Everything to send a factory in one zip: PDF, editable Excel, JSON and reference images."""
    pack_dir = _pack_dir(pack_id)
    version = _version_of(pack_dir, v)
    stem = _file_stem(pack_dir, version)
    pack = TechPack.model_validate_json((pack_dir / f"v{version}.json").read_text())
    meta = _read_meta(pack_dir)
    pdf = _render_pdf(f"http://127.0.0.1:{request.url.port or 80}/packs/{pack_id}?v={version}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}/{stem}.pdf", pdf)
        z.writestr(f"{stem}/{stem}.xlsx", build_xlsx(pack, meta, version))
        z.writestr(f"{stem}/{stem}.json", json.dumps(pack.model_dump(), indent=2))
        for name in _image_names(pack_dir):
            z.write(pack_dir / "images" / name, f"{stem}/reference-images/{name}")
        z.writestr(f"{stem}/READ-ME.txt",
                   f"{pack.style_name} - tech pack, version {version}\n\n"
                   f"Build ONLY to version {version}. Older versions are superseded.\n"
                   f"- {stem}.pdf : the full tech pack (print or view)\n"
                   f"- {stem}.xlsx: editable copy of the tables (BOM, measurements, construction...)\n"
                   f"- {stem}.json: machine-readable data\n"
                   f"- reference-images/: the images the design is based on\n"
                   f"Questions? Quote the style number and version.\n")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{stem}-factory-bundle.zip"'})


def _form_error(request: Request, message: str, form: Optional[dict] = None):
    return templates.TemplateResponse(request, "index.html", {"packs": _list_packs(), "error": message, "form": form or {}}, status_code=400)
