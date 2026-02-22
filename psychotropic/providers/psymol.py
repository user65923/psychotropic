import csv
import json
import re
from importlib import resources
from io import BytesIO, StringIO

import httpx
from PIL import Image, ImageChops
from rdkit import Chem
from rdkit.Chem import rdCoordGen, rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

# Render on an oversized canvas, then auto-crop to fit the molecule.
_RENDER_SIZE = 2000
_PADDING = 12
_TARGET_WIDTH = 600


def load_substances():
    """Load all substances from the bundled psymol CSV.

    Returns a dict mapping substance name to its row dict (url, smiles, etc.).
    """
    text = resources.read_text('psychotropic.data', 'psymol.csv')
    reader = csv.DictReader(StringIO(text))
    return {row['name']: row for row in reader}


def extract_isomerdesign_id(url):
    """Extract the numeric ID from an isomerdesign URL.

    Returns the ID as a string, or None if not an isomerdesign URL.
    """
    match = re.search(r'isomerdesign\.com.*?id=(\d+)', url)
    if match:
        return match.group(1)

    match = re.search(r'isomerdesign\.com/pihkal/explore/(\d+)', url)
    if match:
        return match.group(1)

    return None


async def search_substance(name, client=None):
    """Search isomerdesign for a substance by name.

    Returns the substance ID as an int, or None if not found.
    """
    async def _search(c):
        r = await c.get(
            "https://isomerdesign.com/pihkal/lookup/json",
            params={"q": name},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code != 200:
            return None
        for entry in r.json():
            # Strip HTML bold tags for comparison
            clean = re.sub(r'</?b>', '', entry.get("name", ""))
            if clean.lower() == name.lower():
                return entry["substance_id"]
        return None

    if client:
        return await _search(client)
    async with httpx.AsyncClient() as c:
        return await _search(c)


async def _fetch_molfile_by_id(substance_id, client):
    """Fetch a molfile from an isomerdesign explore page by ID."""
    r = await client.get(
        f"https://isomerdesign.com/pihkal/explore/"
        f"{substance_id}",
        follow_redirects=True,
    )
    if r.status_code != 200:
        return None

    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>',
        r.text,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        data = json.loads(match.group(1), strict=False)
        rep = data.get('hasRepresentation', {})
        if rep.get('name') == 'molfile':
            return rep['value']
    except (json.JSONDecodeError, KeyError):
        pass

    return None


async def fetch_molfile(url_or_name, client=None):
    """Fetch a molfile from isomerdesign.

    Accepts either an isomerdesign URL or a substance name.
    Returns the molfile string, or None on failure.
    """
    async def _fetch(c):
        # Try URL first
        iso_id = extract_isomerdesign_id(url_or_name)

        # Fall back to search by name
        if not iso_id:
            iso_id = await search_substance(
                url_or_name, client=c
            )

        if not iso_id:
            return None

        return await _fetch_molfile_by_id(iso_id, c)

    if client:
        return await _fetch(client)
    async with httpx.AsyncClient() as c:
        return await _fetch(c)


def _render_mol(mol, background_color):
    """Render an RDKit mol object to a tight-fit PIL Image."""
    drawer = rdMolDraw2D.MolDraw2DCairo(_RENDER_SIZE, _RENDER_SIZE)

    opts = drawer.drawOptions()
    opts.bondLineWidth = 3.5
    opts.fixedBondLength = 40
    opts.scaleBondWidth = True
    opts.multipleBondOffset = 0.12
    opts.minFontSize = 18
    opts.maxFontSize = 24
    opts.additionalAtomLabelPadding = 0.15
    opts.padding = 0.01
    opts.useCDKAtomPalette()

    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()

    canvas = Image.open(BytesIO(drawer.GetDrawingText()))
    canvas = canvas.convert("RGB")

    # Auto-crop to molecule bounding box
    bg = Image.new("RGB", canvas.size, background_color)
    diff = ImageChops.difference(canvas, bg)
    bbox = diff.getbbox()
    if not bbox:
        return None

    cropped = canvas.crop(bbox)

    # Scale to target width, preserving aspect ratio
    cw, ch = cropped.size
    scale = _TARGET_WIDTH / cw
    new_w = _TARGET_WIDTH
    new_h = int(ch * scale)
    cropped = cropped.resize(
        (new_w, new_h), Image.LANCZOS
    )

    # Add padding
    result = Image.new(
        "RGB",
        (new_w + 2 * _PADDING, new_h + 2 * _PADDING),
        background_color,
    )
    result.paste(cropped, (_PADDING, _PADDING))

    return result


def generate_from_molfile(molblock, background_color="WHITE"):
    """Generate a molecule image from a molfile block.

    Uses the pre-computed 2D coordinates from the molfile.
    Returns a PIL Image (RGB) or None on failure.
    """
    mol = Chem.MolFromMolBlock(molblock)
    if mol is None:
        return None

    return _render_mol(mol, background_color)


def generate_schematic_image(smiles, background_color="WHITE"):
    """Generate a molecule image from a SMILES string.

    Returns a PIL Image (RGB) or None if the SMILES is invalid.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    # Generate 2D coords with CoordGen for conventional layouts,
    # flip to match PubChem orientation, then straighten
    rdCoordGen.AddCoords(mol)

    conf = mol.GetConformer()
    for i in range(mol.GetNumAtoms()):
        pos = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (-pos.x, -pos.y, pos.z))

    rdDepictor.StraightenDepiction(mol)

    return _render_mol(mol, background_color)
