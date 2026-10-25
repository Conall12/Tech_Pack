"""Calls Claude to turn reference images + a brief into a structured TechPack."""

import base64
import json
import os
from dataclasses import dataclass
from typing import List, Optional

import anthropic

from .schema import TechPack

MODEL = os.environ.get("TECHPACK_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("TECHPACK_EFFORT", "high")

SYSTEM_PROMPT = """You are a senior apparel technical designer who writes tech packs that overseas \
factories (China, Portugal, Turkey, Vietnam, Pakistan, India) sample from directly. You work for a \
small independent clothing brand. The designer gives you AI-generated or reference images plus a brief; \
you produce a complete, production-ready tech pack.

How to work:
- Study every image closely: silhouette, proportions, panel lines, seam placement, topstitching, \
neckline/collar, cuffs, hems, pockets, closures, trims, hardware, graphics, fabric texture and drape. \
AI images often contain impossible or inconsistent details (seams that go nowhere, mismatched sides, \
garbled text). Translate them into something a factory can actually make, and note what you changed \
in the assumptions list.
- The brief from the designer always overrides what the images suggest.
- Use real industry vocabulary: fabric types and GSM, ISO 4915 stitch classes, ISO 4916 seam classes, \
SPI, rib types, print techniques, trim specs.
- Measurements: base them on standard commercial grading for the category and fit, then adjust for the \
silhouette you see. Give every size requested. Use realistic tolerances.
- Construction: cover every seam and operation in sewing order.
- Technical flats: draw clean, symmetrical, factory-style flat sketches as SVG line art. Front and back \
must match in proportions and seam placement. Mark topstitching with dashed lines. Put numbered circle \
callouts on key details, and describe each in the callouts list.
- Never invent brand names, supplier names or trademarked logos. Do not reproduce third-party logos \
from the images.
- Be honest: list every assumption you made and every question the designer should answer before \
sending this to a factory."""


@dataclass
class ReferenceImage:
    data: bytes
    media_type: str
    filename: str


class GenerationError(Exception):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _image_blocks(images: List[ReferenceImage]) -> list:
    blocks = []
    for i, img in enumerate(images, 1):
        blocks.append({"type": "text", "text": f"Reference image {i} ({img.filename}):"})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("utf-8"),
                },
            }
        )
    return blocks


def _brief_text(brief: dict) -> str:
    lines = ["DESIGNER BRIEF"]
    for key, value in brief.items():
        if value:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def _run(content: list) -> TechPack:
    client = _client()
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            output_format=TechPack,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise GenerationError("Anthropic API key missing or invalid. Set ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise GenerationError("Rate limited by the API. Wait a minute and try again.") from e
    except anthropic.APIStatusError as e:
        raise GenerationError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise GenerationError("Could not reach the Anthropic API. Check your connection.") from e

    if message.stop_reason == "refusal":
        raise GenerationError("The model declined this request. Try rewording the brief.")
    if message.stop_reason == "max_tokens":
        raise GenerationError("The tech pack was cut off (too long). Try fewer sizes or a simpler brief.")
    if message.parsed_output is None:
        raise GenerationError("The model did not return a valid tech pack. Please try again.")
    return message.parsed_output


def generate(images: List[ReferenceImage], brief: dict) -> TechPack:
    content = _image_blocks(images)
    content.append(
        {
            "type": "text",
            "text": _brief_text(brief)
            + "\n\nProduce the complete tech pack for this garment.",
        }
    )
    return _run(content)


def revise(previous: TechPack, images: List[ReferenceImage], feedback: str) -> TechPack:
    content = _image_blocks(images)
    content.append(
        {
            "type": "text",
            "text": "CURRENT TECH PACK (JSON):\n"
            + json.dumps(previous.model_dump(), indent=1)
            + "\n\nDESIGNER FEEDBACK / CHANGES:\n"
            + feedback
            + "\n\nReturn the full revised tech pack. Keep everything that the feedback does not "
            "change, apply the changes everywhere they matter (BOM, measurements, construction, flats), "
            "and update the assumptions and questions.",
        }
    )
    return _run(content)


def load_image(path: str) -> Optional[ReferenceImage]:
    ext = os.path.splitext(path)[1].lower()
    media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext)
    if not media:
        return None
    with open(path, "rb") as f:
        return ReferenceImage(f.read(), media, os.path.basename(path))
