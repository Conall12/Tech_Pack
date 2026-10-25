"""Local web app: upload reference images + brief -> printable tech pack."""

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
