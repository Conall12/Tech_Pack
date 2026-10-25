"""Structured tech pack schema. Claude fills this in; templates render it."""

from typing import List

from pydantic import BaseModel, Field, model_validator


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
    pantone: str = Field(description="Pantone TCX reference for this item (approximate), or N/A for undyed/natural items")
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
    seam_allowance: str = Field(description="Seam allowance in the pack units, e.g. 1.0 cm; N/A if not applicable")
    callout_ref: str = Field(description="Callout number(s) on the flats this step relates to, e.g. 3, 5; empty if none")
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
    size: str = Field(description="Label dimensions in the pack units")
    content: str = Field(description="Exact text/artwork on the label (never a third-party brand)")
    placement: str


class TestRequirement(BaseModel):
    test: str = Field(description="e.g. Dimensional stability after wash, Colourfastness to rubbing, Seam strength")
    standard: str = Field(description="Test method, e.g. ISO 6330 / ISO 105-X12 / ISO 13935-2")
    requirement: str = Field(description="Pass criteria, e.g. max 3% shrinkage")


class PackingSpec(BaseModel):
    folding: str
    polybag: str = Field(description="Polybag size/spec, suffocation warning, recycled content if relevant")
    hangtag_and_stickers: str
    units_per_carton: str
    packing_ratio: str = Field(description="Size ratio per carton, e.g. S1:M2:L2:XL1")
    carton_marking: str = Field(description="Shipping mark / carton labelling requirements")


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
    packing_details: PackingSpec
    quality_requirements: List[str]

    fit_notes: List[str] = Field(
        description="Fit comments for the factory: intended silhouette, block/pattern reference, model size, "
        "ease, key fit points and what to avoid"
    )
    sampling_requirements: List[str] = Field(
        description="Sample stages needed (proto, fit, SMS, PP, TOP), what each must show and what the "
        "designer will approve at each; lab dips / strike-offs for colours and prints"
    )
    testing_requirements: List[TestRequirement]
    country_of_origin: str = Field(description="Placeholder 'Made in [country]' text for the label; factory country if known")
    compliance_notes: List[str] = Field(
        description="Care-symbol standard (ISO 3758), fibre-content labelling, restricted substances, "
        "and any market-specific labelling (UK/EU/US) the designer must confirm"
    )

    assumptions: List[str] = Field(
        description="Everything you inferred or guessed rather than read from the brief/images. Be thorough."
    )
    questions_for_designer: List[str] = Field(
        description="Open questions the designer should answer before sending to a factory"
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_legacy(cls, data):
        """Packs saved before these fields existed still need to load and render."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("fit_notes", "sampling_requirements", "testing_requirements", "compliance_notes"):
            data.setdefault(key, [])
        data.setdefault("country_of_origin", "")
        data.setdefault("packing_details", {k: "" for k in PackingSpec.model_fields})
        for row in data.get("bill_of_materials", []):
            row.setdefault("pantone", "")
        for row in data.get("construction", []):
            row.setdefault("seam_allowance", "")
            row.setdefault("callout_ref", "")
        for row in data.get("labels_and_trims", []):
            row.setdefault("size", "")
            row.setdefault("content", "")
        return data

