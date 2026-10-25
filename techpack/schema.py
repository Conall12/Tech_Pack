"""Structured tech pack schema. Claude fills this in; templates render it."""

from typing import List

from pydantic import BaseModel, Field


class Callout(BaseModel):
    number: int = Field(description="Callout number shown on the flat sketch")
    label: str
    description: str


class BomItem(BaseModel):
    component: str = Field(description="e.g. Body fabric, Rib, Zip, Drawcord, Main label")
    material: str = Field(description="e.g. Brushed-back loopback fleece, YKK #5 metal zip")
    composition: str = Field(description="Fibre content, e.g. 100% cotton; N/A for hardware")
    weight_or_size: str = Field(description="GSM for fabric, gauge/size for trims")
    color: str
    placement: str
    quantity: str = Field(description="Per garment, e.g. 1 pc, 0.45 m")
    notes: str


class Colorway(BaseModel):
    name: str
    pantone: str = Field(description="Closest Pantone TCX reference, marked approximate")
    applies_to: str = Field(description="Which components use this colour")


class MeasurementPoint(BaseModel):
    code: str = Field(description="POM code, e.g. A, B, C")
    description: str = Field(description="How to measure, e.g. 'Chest width, 2cm below armhole, laid flat'")
    tolerance: str = Field(description="+/- tolerance in the pack's units")
    values: List[str] = Field(description="One value per size, in the same order as the sizes list")


class ConstructionStep(BaseModel):
    area: str = Field(description="e.g. Shoulder seam, Hood attachment, Hem")
    operation: str
    stitch_type: str = Field(description="ISO 4915 class where possible, e.g. '504 3-thread overlock'")
    seam_type: str = Field(description="ISO 4916 class or plain description")
    spi: str = Field(description="Stitches per inch / per cm")
    notes: str


class Artwork(BaseModel):
    name: str
    technique: str = Field(description="e.g. Screen print plastisol, puff print, embroidery, DTG")
    size: str
    placement: str = Field(description="Placement with measurements from reference points, e.g. 8cm below HPS, centred")
    colors: str
    notes: str


class LabelTrim(BaseModel):
    item: str = Field(description="e.g. Main neck label, Care label, Size label, Hang tag")
    spec: str
    placement: str


class TechPack(BaseModel):
    style_name: str
    style_number: str
    season: str
    category: str = Field(description="e.g. Hoodie, Tee, Cargo pant")
    gender_fit: str = Field(description="e.g. Unisex, oversized fit")
    summary: str = Field(description="2-4 sentence description of the garment for the factory")
    key_design_details: List[str]

    front_flat_svg: str = Field(
        description="Complete standalone <svg> technical flat, FRONT view. Black line art on white, "
        "viewBox='0 0 400 500', stroke-width 1.5, no fill except white, dashed lines for topstitching, "
        "numbered circle callouts matching the callouts list. No text other than callout numbers."
    )
    back_flat_svg: str = Field(description="Same rules as front_flat_svg, BACK view, consistent proportions with the front.")
    callouts: List[Callout]

    bill_of_materials: List[BomItem]
    colorways: List[Colorway]

    units: str = Field(description="cm or in")
    base_size: str
    sizes: List[str]
    measurements: List[MeasurementPoint]

    construction: List[ConstructionStep]
    artwork: List[Artwork]
    labels_and_trims: List[LabelTrim]

    wash_and_finishing: str
    care_instructions: List[str]
    packaging: str
    quality_requirements: List[str]

    assumptions: List[str] = Field(
        description="Everything you inferred or guessed rather than read from the brief/images. Be thorough."
    )
    questions_for_designer: List[str] = Field(
        description="Open questions the designer should answer before sending to a factory"
    )
