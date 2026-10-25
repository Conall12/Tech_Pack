# Tech Pack Generator

A local web app for your own clothing brand. Upload reference images (AI-generated or real), describe the garment, and Claude writes a factory-ready tech pack. You can print it or save it as a PDF to send to manufacturers.

Each tech pack includes:

1. **Cover**: style info, summary, key design details and your reference images
2. **Technical flats**: front and back line drawings with numbered callouts
3. **Bill of materials and colourways** (approximate Pantone TCX references)
4. **Points of measure and grading** across all your sizes, with tolerances
5. **Construction**: every operation with ISO stitch and seam types and SPI
6. **Artwork, labels, trims, wash, care, packaging and quality requirements**

There is also an on-screen-only checklist of the **assumptions** Claude made and the **questions** you should answer before sending the pack to a factory.

## Setup

You need Python 3.10+ and **either** Claude Code (logged in, uses your Claude subscription, no API credits) **or** an Anthropic API key with credits.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt   # run from the repo root
# only needed for TECHPACK_BACKEND=api:
# export ANTHROPIC_API_KEY=sk-ant-...
uvicorn techpack.app:app --port 8000
```

Then open http://localhost:8000.

## Using it

1. Upload one or more reference images. Front, back and detail close-ups all help.
2. Fill in whatever you know: fabric, fit, sizes, colours, graphics, trims. Anything you leave blank, Claude infers and lists under "Assumptions".
3. Click **Generate**. It takes about 1–4 minutes.
4. Review the pack, especially the assumptions, questions and measurements.
5. Use **Revise** to give feedback in plain English, e.g. "make the hood double-layer, 400gsm instead". Each revision is saved as a new version, and earlier versions are kept.
6. Click **Print / Save PDF**. The print layout is A4 landscape, one section per page, and the assumptions page is left out.

Packs are saved in `packs/` at the repo root, one folder per style containing the reference images, every version as JSON, and the history.

## Settings (optional environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `TECHPACK_BACKEND` | `claude-code` | `claude-code` runs the local `claude` CLI on your subscription; `api` calls the Anthropic API |
| `TECHPACK_CLI_MODEL` | CLI default | Model for the `claude-code` backend |
| `TECHPACK_CLI_TIMEOUT` | `900` | Seconds before the CLI run is abandoned |
| `TECHPACK_MODEL` | `claude-opus-5-5` | Model used by the `api` backend |
| `TECHPACK_EFFORT` | `high` | `medium` is faster and cheaper; `xhigh` is more thorough |
| `TECHPACK_DATA` | `./packs` | Where packs are stored |

## Good to know

- **Check measurements before sampling.** They are based on standard commercial grading for the category and fit, adjusted to your images. Compare them against a garment you like the fit of.
- **The flats are AI-drawn SVG line art.** They are good for communicating the design, but have them cleaned up in Illustrator if a factory asks for precise CADs.
- AI images often show details that can't be constructed. Claude translates these into something makeable and notes each change under the assumptions.
