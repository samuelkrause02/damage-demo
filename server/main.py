"""Minimal API: PDF, Vertrags-Speicher (Europcar-Style)."""

from __future__ import annotations

import base64
import json
import os
import re
import struct
from collections import defaultdict
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field

try:
    from .blob_store import contract_path as blob_contract_path
    from .blob_store import get_bytes as blob_get_bytes
    from .blob_store import get_json as blob_get_json
    from .blob_store import is_configured as blob_configured
    from .blob_store import photo_path as blob_photo_path
    from .blob_store import put_bytes as blob_put_bytes
    from .blob_store import put_json as blob_put_json
except ImportError:
    from blob_store import contract_path as blob_contract_path
    from blob_store import get_bytes as blob_get_bytes
    from blob_store import get_json as blob_get_json
    from blob_store import is_configured as blob_configured
    from blob_store import photo_path as blob_photo_path
    from blob_store import put_bytes as blob_put_bytes
    from blob_store import put_json as blob_put_json

app = FastAPI(title="Damage Demo API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = Path(__file__).resolve().parent

def _vehicle_assets_dir() -> Path | None:
    for candidate in (
        SERVER_ROOT / "assets" / "vehicles",
        DEMO_ROOT / "public" / "assets" / "vehicles",
        DEMO_ROOT / "assets" / "vehicles",
    ):
        if candidate.exists():
            return candidate
    return None
CONTRACTS_DIR = Path(__file__).resolve().parent / "data" / "contracts"


def _contracts_enabled() -> bool:
    if blob_configured():
        return True
    return not os.getenv("VERCEL")


if _contracts_enabled():
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

ZONES = ["Front", "Heck", "Fahrer", "Beifahrer", "Dach", "Innenraum", "Sonstiges"]
VIEW_LABELS = {
    "top": "Dach",
    "front": "Front",
    "rear": "Heck",
    "driver": "Fahrer",
    "passenger": "Beifahrer",
    "interior": "Innenraum",
}
INTERIOR_LABELS = {
    "dashboard": "Armaturenbrett",
    "seat_driver": "Fahrersitz",
    "seat_passenger": "Beifahrersitz",
    "door_trim": "Tuerverkleidung",
    "cargo": "Laderaum",
    "ceiling": "Dachhimmel",
    "other": "Sonstiges (innen)",
}
ZONE_SKETCH_KEY = {
    "Front": "front",
    "Heck": "rear",
    "Fahrer": "driver",
    "Beifahrer": "passenger",
    "Dach": "top",
}
ZONE_ASSET = {
    "Front": "front.png",
    "Heck": "rear.png",
    "Fahrer": "driver.png",
    "Beifahrer": "passenger.png",
    "Dach": "top.png",
}
# viewBox-Groessen (Schadenkoordinaten aus dem Frontend)
VIEWBOX = {
    "Front": (720, 580),
    "Heck": (720, 572),
    "Fahrer": (720, 286),
    "Beifahrer": (720, 286),
    "Dach": (720, 358),
}
# Max. Skizzenbox im PDF (mm), proportional skaliert
ZONE_MARKER_SCALE = {
    "Front": 1.5,
    "Heck": 1.5,
}
SKETCH_LIMITS = {
    "Front": (36, 46),
    "Heck": (36, 46),
    "Fahrer": (52, 24),
    "Beifahrer": (52, 24),
    "Dach": (52, 28),
    "Innenraum": (0, 18),
    "Sonstiges": (36, 36),
}

MARGIN = 15
COL_W = 88
COL_GAP = 10
LEFT_X = MARGIN
RIGHT_X = MARGIN + COL_W + COL_GAP


class DamageItem(BaseModel):
    number: int
    desc: str
    type: str = "new"
    photo: str | None = None
    x: float | None = None
    y: float | None = None
    view: str = "top"
    interior_area: str | None = None


class GeneratePdfRequest(BaseModel):
    kennzeichen: str = ""
    datum: str = ""
    kunde: str = ""
    fahrzeug: str = ""
    vertragsnummer: str = ""
    station: str = ""
    km_abholung: str = ""
    km_rueckgabe: str = ""
    tank_abholung: str = ""
    tank_rueckgabe: str = ""
    kommentar: str = ""
    protocol_mode: str = "return"
    damages: list[DamageItem] = Field(default_factory=list)
    car_image: str | None = None
    car_images: dict[str, str] = Field(default_factory=dict)


class ContractPickupRequest(BaseModel):
    vertragsnummer: str
    kennzeichen: str = ""
    kunde: str = ""
    fahrzeug: str = ""
    station: str = ""
    datum: str = ""
    km: str = ""
    tank: str = "8 / 8"
    damages: list[DamageItem] = Field(default_factory=list)


class ContractReturnRequest(BaseModel):
    vertragsnummer: str
    kennzeichen: str = ""
    kunde: str = ""
    fahrzeug: str = ""
    station: str = ""
    datum: str = ""
    km_abholung: str = ""
    km_rueckgabe: str = ""
    tank_abholung: str = "8 / 8"
    tank_rueckgabe: str = "7 / 8"
    damages: list[DamageItem] = Field(default_factory=list)


class PhotoUploadRequest(BaseModel):
    data_url: str
    vertragsnummer: str = ""
    damage_number: int = 0


def _sanitize_contract_id(vertragsnummer: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", vertragsnummer.strip())
    return safe or "unknown"


def _contract_path(vertragsnummer: str) -> Path:
    return CONTRACTS_DIR / f"{_sanitize_contract_id(vertragsnummer)}.json"


def _load_contract_file(vertragsnummer: str) -> dict | None:
    contract_id = _sanitize_contract_id(vertragsnummer)
    if blob_configured():
        return blob_get_json(blob_contract_path(contract_id))
    path = _contract_path(vertragsnummer)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _save_contract_file(vertragsnummer: str, data: dict) -> None:
    contract_id = _sanitize_contract_id(vertragsnummer)
    if blob_configured():
        blob_put_json(blob_contract_path(contract_id), data)
        return
    path = _contract_path(vertragsnummer)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _persist_damage_photos(
    vertragsnummer: str,
    damages: list[DamageItem],
    force_type: str | None = "known",
) -> list[dict]:
    contract_id = _sanitize_contract_id(vertragsnummer)
    items: list[dict] = []
    for d in damages:
        item = {**d.model_dump()}
        if force_type is not None:
            item["type"] = force_type
        photo = item.get("photo")
        if photo and photo.startswith("data:image") and blob_configured():
            decoded = _decode_image(photo)
            if decoded:
                _, raw = decoded
                try:
                    item["photo"] = blob_put_bytes(
                        blob_photo_path(contract_id, d.number),
                        _normalize_photo_bytes(raw),
                        "image/jpeg",
                    )
                except Exception:
                    pass
        items.append(item)
    return items


def _photo_present(photo: str | None) -> bool:
    if not photo:
        return False
    return photo.startswith("data:image") or photo.startswith("http://") or photo.startswith("https://")


def _resolve_photo_bytes(photo: str) -> bytes | None:
    if not photo:
        return None
    if photo.startswith("data:image"):
        decoded = _decode_image(photo)
        return _normalize_photo_bytes(decoded[1]) if decoded else None
    if photo.startswith("http://") or photo.startswith("https://"):
        if blob_configured():
            raw = blob_get_bytes(photo)
            if raw:
                return _normalize_photo_bytes(raw)
        try:
            from urllib.request import urlopen

            with urlopen(photo, timeout=20) as resp:
                return _normalize_photo_bytes(resp.read())
        except Exception:
            return None
    return None


def _decode_image(data_url: str) -> tuple[str, bytes] | None:
    if not data_url or not data_url.startswith("data:image"):
        return None
    match = re.match(r"data:image/(png|jpe?g|webp);base64,(.+)", data_url, re.I | re.S)
    if not match:
        return None
    ext = "png" if match.group(1).lower().startswith("png") else "jpg"
    try:
        raw = base64.b64decode(match.group(2))
    except Exception:
        return None
    if not raw:
        return None
    return ext, raw


def _normalize_photo_bytes(raw: bytes) -> bytes:
    """EXIF-Orientierung anwenden (Handy-Fotos) und als JPEG fuer fpdf2 ausgeben."""
    im = ImageOps.exif_transpose(Image.open(BytesIO(raw)))
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _latin(text: str) -> str:
    return (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ß", "ss")
    )


def zone_from_coords(x: float | None, y: float | None) -> str:
    if x is None or y is None:
        return "Sonstiges"
    # Draufsicht 720x358, Front links
    if x < 200:
        return "Front"
    if x > 520:
        return "Heck"
    if y < 110:
        return "Beifahrer"
    if y > 250:
        return "Fahrer"
    return "Dach"


def _interior_label(area: str | None) -> str:
    if not area:
        return "Innenraum"
    return INTERIOR_LABELS.get(area, area)


def _damage_line(d: DamageItem) -> str:
    if (d.view or "").lower() == "interior":
        return f"#{d.number}  {_interior_label(d.interior_area)}: {d.desc}"
    return f"#{d.number}  {d.desc}"


def zone_for_damage(d: DamageItem) -> str:
    view = (d.view or "top").lower()
    if view == "interior":
        return "Innenraum"
    if view == "front":
        return "Front"
    if view == "rear":
        return "Heck"
    if view == "driver":
        return "Fahrer"
    if view == "passenger":
        return "Beifahrer"
    if view == "top":
        return zone_from_coords(d.x, d.y)
    return "Sonstiges"


def group_by_zone(damages: list[DamageItem]) -> dict[str, list[DamageItem]]:
    grouped: dict[str, list[DamageItem]] = defaultdict(list)
    for d in damages:
        grouped[zone_for_damage(d)].append(d)
    return grouped


class EuropcarPDF(FPDF):
    def __init__(self, meta: dict):
        super().__init__()
        self.meta = meta

    def footer(self):
        self.set_y(-20)
        self.set_draw_color(200, 200, 200)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(60, 60, 60)
        station = _latin(self.meta.get("station", "Pilot"))
        self.cell(0, 3, f"RUECKGABEPROTOKOLL  |  {station}", ln=True)
        self.set_font("Helvetica", "", 7)
        footer = (
            f"Vertragsnr.: {self.meta.get('vertragsnummer', '-')}    "
            f"Kennzeichen: {self.meta.get('kennzeichen', '-')}    "
            f"Rueckgabe: {self.meta.get('datum', '-')}    "
            f"Seite {self.page_no()}/{{nb}}"
        )
        self.cell(0, 3, _latin(footer), ln=True)
        self.set_text_color(0, 0, 0)

    def page_header(self, title: str):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(20, 20, 20)
        self.cell(0, 9, _latin(title), ln=True)
        self.set_draw_color(30, 30, 30)
        self.set_line_width(0.4)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(6)
        self.set_text_color(0, 0, 0)

    def section_title(self, title: str):
        self.ln(2)
        self.set_fill_color(240, 240, 240)
        self.set_draw_color(210, 210, 210)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 6, _latin(f"  {title}"), ln=True, fill=True)
        self.ln(3)

    def _field_height(self, x: float, y: float, label: str, value: str, w: float) -> float:
        self.set_xy(x, y)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(120, 120, 120)
        self.cell(w, 3.5, _latin(label), ln=1)
        self.set_x(x)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(0, 0, 0)
        self.multi_cell(w, 4.5, _latin(value or "-"))
        return self.get_y() - y

    def info_pair(self, left: tuple[str, str], right: tuple[str, str] | None = None):
        y0 = self.get_y()
        h_left = self._field_height(LEFT_X, y0, left[0], left[1], COL_W)
        h_right = 0.0
        if right:
            h_right = self._field_height(RIGHT_X, y0, right[0], right[1], COL_W)
        self.set_xy(MARGIN, y0 + max(h_left, h_right) + 4)

    def zone_row_europcar(self, zone: str, items: list[DamageItem], sketch: bytes | None = None):
        label_w = 22
        sketch_x = MARGIN + label_w + 2
        max_w, max_h = SKETCH_LIMITS.get(zone, (36, 36))
        row_min_h = max(max_h, 18) + 6

        _ensure_space(self, row_min_h + 6)
        y0 = self.get_y()

        self.set_xy(MARGIN, y0 + 6)
        self.set_font("Helvetica", "B", 9)
        self.cell(label_w, 5, _latin(zone))

        img_w = img_h = 0.0
        if sketch:
            img_w, img_h = _fit_mm(sketch, max_w, max_h)
            # Nie ueber Seitenrand
            right_limit = self.w - MARGIN - 80
            if sketch_x + img_w > right_limit:
                img_w, img_h = _fit_mm(sketch, right_limit - sketch_x, max_h)
            img_h = _draw_image_box(self, sketch, sketch_x, y0, img_w, img_h)

        text_x = sketch_x + (img_w if img_w else max_w) + 5
        text_w = max(self.w - MARGIN - text_x, 60)

        self.set_xy(text_x, y0 + 4)
        if not items:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            self.multi_cell(text_w, 4.5, "Kein Schaden in diesem Bereich")
            self.set_text_color(0, 0, 0)
        else:
            self.set_font("Helvetica", "", 8)
            for d in items:
                self.set_x(text_x)
                self.multi_cell(text_w, 4.5, _latin(_damage_line(d)))
                self.ln(0.5)

        content_h = max(img_h, max_h * 0.6, self.get_y() - y0)
        self.set_y(y0 + content_h + 4)
        self.set_draw_color(220, 220, 220)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.ln(3)


def _km_driven(ab: str, rb: str) -> str:
    try:
        if ab and rb:
            a = int(re.sub(r"[^\d]", "", ab))
            b = int(re.sub(r"[^\d]", "", rb))
            return f"{b - a} KM"
    except ValueError:
        pass
    return "-"


def _fmt_date(d: str) -> str:
    if re.match(r"^\d{4}-\d{2}-\d{2}", d):
        y, m, day = d.split("-")
        return f"{day}.{m}.{y}"
    return d or date.today().strftime("%d.%m.%Y")


def _image_pixel_size(raw: bytes) -> tuple[int, int]:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", raw[16:24])
        return int(w), int(h)
    if raw[:2] == b"\xff\xd8":
        i = 2
        while i < len(raw) - 8:
            if raw[i] != 0xFF:
                break
            marker = raw[i + 1]
            i += 2
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", raw[i + 3 : i + 7])
                return int(w), int(h)
            length = struct.unpack(">H", raw[i : i + 2])[0]
            i += length
    return 4, 3


def _fit_mm(raw: bytes, max_w: float, max_h: float) -> tuple[float, float]:
    """Skaliert Bild proportional in eine Box (mm), ohne Verzerrung."""
    px_w, px_h = _image_pixel_size(raw)
    if px_w <= 0 or px_h <= 0:
        return max_w, max_h
    aspect = px_w / px_h
    w = max_w
    h = w / aspect
    if h > max_h:
        h = max_h
        w = h * aspect
    return w, h


def _ensure_space(pdf: EuropcarPDF, needed: float):
    if pdf.get_y() + needed > pdf.page_break_trigger:
        pdf.add_page()


def _draw_image_box(pdf: EuropcarPDF, raw: bytes, x: float, y: float, w: float, h: float) -> float:
    """Zeichnet Bild mit festen mm-Massen (bereits proportional skaliert)."""
    if w <= 0 or h <= 0:
        return 0.0
    if x + w > pdf.w - MARGIN:
        scale = (pdf.w - MARGIN - x) / w
        w *= scale
        h *= scale
    pdf.image(BytesIO(raw), x=x, y=y, w=w, h=h)
    return h


def _marker_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(12, min(size, 56))
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _draw_damage_marker(
    draw: ImageDraw.ImageDraw,
    px: float,
    py: float,
    r: int,
    number: int,
    fill: tuple[int, int, int, int],
) -> None:
    draw.ellipse(
        [px - r, py - r, px + r, py + r],
        fill=fill,
        outline=(255, 255, 255, 255),
        width=max(2, r // 8),
    )
    font_size = max(18, int(r * 1.05))
    font = _marker_font(font_size)
    draw.text(
        (px, py),
        str(number),
        fill=(255, 255, 255, 255),
        anchor="mm",
        font=font,
    )


def _decode_sketch_image(data_url: str) -> Image.Image | None:
    decoded = _decode_image(data_url)
    if not decoded:
        return None
    _, raw = decoded
    return Image.open(BytesIO(raw)).convert("RGBA")


def _render_zone_sketch(
    zone: str,
    damages: list[DamageItem],
    color: str = "new",
    car_images: dict[str, str] | None = None,
) -> bytes | None:
    """Skizze serverseitig aus Asset + Schadenmarkierungen (kein Client-Export)."""
    asset = ZONE_ASSET.get(zone)
    if not asset:
        return None

    assets_dir = _vehicle_assets_dir()
    path = assets_dir / asset if assets_dir else None
    from_client = False
    if path and path.exists():
        im = Image.open(path).convert("RGBA")
    elif car_images and ZONE_SKETCH_KEY.get(zone) in car_images:
        im = _decode_sketch_image(car_images[ZONE_SKETCH_KEY[zone]])
        if im is None:
            return None
        from_client = True
    else:
        return None
    iw, ih = im.size
    vb_w, vb_h = VIEWBOX.get(zone, (iw, ih))
    sx, sy = iw / vb_w, ih / vb_h

    zone_damages = [d for d in damages if zone_for_damage(d) == zone]
    if zone_damages and not from_client:
        draw = ImageDraw.Draw(im)
        r = max(13, min(iw, ih) // 26)
        r = int(r * ZONE_MARKER_SCALE.get(zone, 1.0))
        fill = (100, 116, 139, 240) if color == "known" else (220, 38, 38, 240)
        for d in zone_damages:
            if d.x is None or d.y is None:
                continue
            px, py = d.x * sx, d.y * sy
            _draw_damage_marker(draw, px, py, r, d.number, fill)

    buf = BytesIO()
    im.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def build_pdf(payload: GeneratePdfRequest) -> bytes:
    datum = _fmt_date(payload.datum)
    now_time = datetime.now().strftime("%H:%M")
    meta = {
        "kennzeichen": payload.kennzeichen or "-",
        "datum": datum,
        "station": payload.station or "Pilot",
        "vertragsnummer": payload.vertragsnummer or "DEMO-001",
    }
    known = [d for d in payload.damages if d.type == "known"]
    new_damages = [d for d in payload.damages if d.type != "known"]
    grouped_known = group_by_zone(known)
    grouped_new = group_by_zone(new_damages)
    is_pickup = payload.protocol_mode == "pickup"
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    pdf = EuropcarPDF(meta)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)

    title = "Uebergabeprotokoll" if is_pickup else "Rueckgabeprotokoll"
    pdf.add_page()
    pdf.page_header(title)

    pdf.section_title("Fahrzeuginformationen")
    pdf.info_pair(
        ("Fahrzeugmodell", payload.fahrzeug or "-"),
        ("Kunde", payload.kunde or "-"),
    )
    pdf.info_pair(
        ("Kennzeichen", payload.kennzeichen or "-"),
        ("Vertragsnummer", payload.vertragsnummer or "DEMO-001"),
    )
    pdf.info_pair(("Kraftstoffart", "Diesel"), None)

    pdf.section_title("Vertragsinformationen")
    pdf.info_pair(
        ("Anmietstation", payload.station or "-"),
        ("Gepl. Rueckgabestation", payload.station or "-"),
    )
    pdf.info_pair(
        ("Abholung", f"{datum}  08:30"),
        ("Gepl. Rueckgabe", f"{datum}  18:00"),
    )
    pdf.info_pair(
        ("Tatsaechliche Rueckgabe", f"{datum}  {now_time}"),
        None,
    )

    pdf.section_title("Fahrzeugdaten")
    pdf.info_pair(
        ("Tank/Batterie Abholung", payload.tank_abholung or "8 / 8"),
        ("Tank/Batterie Rueckgabe", payload.tank_rueckgabe or "8 / 8"),
    )
    pdf.info_pair(
        ("KM Abholung", payload.km_abholung or "-"),
        ("KM Rueckgabe", payload.km_rueckgabe or "-"),
    )
    pdf.info_pair(("Gefahrene KM", _km_driven(payload.km_abholung, payload.km_rueckgabe)), None)

    # Page 2: Bekannte + Neuschaeden (Europcar: Skizze + Text)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "Schadenuebersicht", ln=True)
    pdf.ln(2)

    pdf.section_title("Bekannte Schaeden bei Anmietung")
    for zone in ZONES:
        pdf.zone_row_europcar(
            zone,
            grouped_known.get(zone, []),
            _render_zone_sketch(zone, known, color="known", car_images=payload.car_images),
        )

    if not is_pickup:
        pdf.section_title("Neuschaden bei Rueckgabe")
        for zone in ZONES:
            pdf.zone_row_europcar(
                zone,
                grouped_new.get(zone, []),
                _render_zone_sketch(zone, new_damages, color="new", car_images=payload.car_images),
            )

    # Page 3: Fotos
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    photo_title = "Schadenfotos" if is_pickup else "Neue Schadenfotos"
    pdf.cell(0, 7, photo_title, ln=True)
    pdf.ln(3)

    photo_damages = known if is_pickup else new_damages
    with_photos = [d for d in photo_damages if _photo_present(d.photo)]
    if not with_photos:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(140, 140, 140)
        pdf.cell(0, 8, "Keine Fotos dokumentiert.", ln=True)
        pdf.set_text_color(0, 0, 0)
    else:
        for zone in ZONES:
            zone_items = [d for d in with_photos if zone_for_damage(d) == zone]
            if not zone_items:
                continue
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(240, 240, 240)
            pdf.cell(0, 6, _latin(f"  {zone}"), ln=True, fill=True)
            pdf.ln(2)
            for d in zone_items:
                view_key = (d.view or "top").lower()
                if view_key == "interior":
                    view_label = f"Innenraum / {_interior_label(d.interior_area)}"
                else:
                    view_label = VIEW_LABELS.get(view_key, d.view or "")
                pdf.set_x(MARGIN)
                pdf.set_font("Helvetica", "B", 9)
                pdf.cell(0, 5, _latin(f"Schaden #{d.number}"), ln=True)
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(80, 80, 80)
                pdf.cell(0, 4, _latin(f"{view_label}  |  {_latin(d.desc)}"), ln=True)
                pdf.set_text_color(0, 0, 0)
                raw = _resolve_photo_bytes(d.photo or "")
                if raw:
                    try:
                        max_photo_w, max_photo_h = 70.0, 52.0
                        _ensure_space(pdf, max_photo_h + 16)
                        y_img = pdf.get_y() + 1
                        x_img = MARGIN
                        fit_w, fit_h = _fit_mm(raw, max_photo_w, max_photo_h)
                        img_h = _draw_image_box(pdf, raw, x_img, y_img, fit_w, fit_h)
                        caption_x = min(x_img + fit_w + 4, pdf.w - MARGIN - 35)
                        pdf.set_xy(caption_x, y_img)
                        pdf.set_font("Helvetica", "", 7)
                        pdf.set_text_color(120, 120, 120)
                        caption_w = pdf.w - MARGIN - caption_x
                        pdf.multi_cell(max(caption_w, 30), 4, f"Aufgenommen:\n{ts}")
                        pdf.set_text_color(0, 0, 0)
                        pdf.set_y(y_img + img_h + 4)
                    except Exception:
                        pdf.ln(4)
                pdf.ln(3)

    # Page 4: Kommentar + Unterschriften
    pdf.add_page()
    pdf.section_title("Kommentar zum Unfall")
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, _latin(payload.kommentar or "Unfall waehrend der Fahrt?  Nein"))
    pdf.ln(6)

    pdf.section_title("Unterschriften")
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(
        0,
        4.5,
        _latin(
            "Mit ihrer Unterschrift bestaetigen Kunde und Mitarbeiter die Richtigkeit "
            "der dokumentierten Fahrzeug- und Schadensdaten. Alle Angaben vorbehaltlich."
        ),
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 4, f"Dokument erstellt: {ts}", ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(10)

    sig_w = (pdf.w - 2 * MARGIN - 10) / 2
    y_sig = pdf.get_y()

    for i, label in enumerate(["Unterschrift Kunde", "Unterschrift Mitarbeiter"]):
        x = MARGIN + i * (sig_w + 10)
        pdf.set_xy(x, y_sig)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(sig_w, 5, _latin(label), ln=1)
        pdf.set_xy(x, y_sig + 20)
        pdf.set_draw_color(160, 160, 160)
        pdf.line(x, pdf.get_y(), x + sig_w - 5, pdf.get_y())
        pdf.set_xy(x, y_sig + 24)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(140, 140, 140)
        pdf.cell(sig_w, 4, "Datum / Ort", ln=1)
        pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


if os.getenv("VERCEL"):
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/index.html", status_code=307)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/env")
def env():
    return {
        "contracts": _contracts_enabled(),
        "pdf": True,
        "blob": blob_configured(),
    }


@app.post("/api/contracts")
def save_pickup(data: ContractPickupRequest):
    if not _contracts_enabled():
        raise HTTPException(status_code=503, detail="Vertragsspeicher nur lokal verfügbar")
    nr = data.vertragsnummer.strip()
    if not nr:
        raise HTTPException(status_code=400, detail="Vertragsnummer fehlt")

    record = _load_contract_file(nr) or {"vertragsnummer": nr, "pickup": None, "return": None}
    record["pickup"] = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "kennzeichen": data.kennzeichen,
        "kunde": data.kunde,
        "fahrzeug": data.fahrzeug,
        "station": data.station,
        "datum": data.datum,
        "km": data.km,
        "tank": data.tank,
        "damages": _persist_damage_photos(nr, data.damages),
    }
    _save_contract_file(nr, record)
    return {"ok": True, "vertragsnummer": nr}


@app.post("/api/contracts/return")
def save_return(data: ContractReturnRequest):
    if not _contracts_enabled():
        raise HTTPException(status_code=503, detail="Vertragsspeicher nur lokal verfügbar")
    nr = data.vertragsnummer.strip()
    if not nr:
        raise HTTPException(status_code=400, detail="Vertragsnummer fehlt")

    record = _load_contract_file(nr)
    if not record or not record.get("pickup"):
        raise HTTPException(status_code=400, detail="Keine Übergabedaten – zuerst Übergabe speichern")

    record["return"] = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "kennzeichen": data.kennzeichen,
        "kunde": data.kunde,
        "fahrzeug": data.fahrzeug,
        "station": data.station,
        "datum": data.datum,
        "km_abholung": data.km_abholung,
        "km_rueckgabe": data.km_rueckgabe,
        "tank_abholung": data.tank_abholung,
        "tank_rueckgabe": data.tank_rueckgabe,
        "damages": _persist_damage_photos(nr, data.damages, force_type=None),
    }
    _save_contract_file(nr, record)
    return {"ok": True, "vertragsnummer": nr}


@app.post("/api/photos")
def upload_photo(body: PhotoUploadRequest):
    if not blob_configured():
        raise HTTPException(status_code=503, detail="Blob Storage nicht konfiguriert")
    decoded = _decode_image(body.data_url)
    if not decoded:
        raise HTTPException(status_code=400, detail="Ungültiges Bild")
    _, raw = decoded
    contract_id = _sanitize_contract_id(body.vertragsnummer) if body.vertragsnummer.strip() else "temp"
    damage_number = body.damage_number or int(datetime.now().timestamp())
    try:
        url = blob_put_bytes(
            blob_photo_path(contract_id, damage_number),
            _normalize_photo_bytes(raw),
            "image/jpeg",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"url": url}


@app.get("/api/contracts/{vertragsnummer}")
def load_contract(vertragsnummer: str):
    if not _contracts_enabled():
        raise HTTPException(status_code=503, detail="Vertragsspeicher nur lokal verfügbar")
    record = _load_contract_file(vertragsnummer)
    if not record or not record.get("pickup"):
        raise HTTPException(status_code=404, detail="Vertrag nicht gefunden")
    return record


@app.post("/api/generate-pdf")
def generate_pdf(payload: GeneratePdfRequest):
    try:
        pdf_bytes = build_pdf(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    prefix = "uebergabeprotokoll" if payload.protocol_mode == "pickup" else "rueckgabeprotokoll"
    filename = f"{prefix}_{payload.kennzeichen or 'demo'}.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if not os.getenv("VERCEL"):
    _static_dir = DEMO_ROOT / "public" if (DEMO_ROOT / "public" / "index.html").exists() else DEMO_ROOT
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="demo")
