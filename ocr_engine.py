"""Extracción orientativa de marcas horarias de conversaciones de WhatsApp.

No usa EXIF, fechas de descarga ni metadatos. No puede certificar autenticidad.
Incluye preprocesamiento para Modo Oscuro y optimización de memoria.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from typing import Any

import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

# Admite 6:00, 06:00, 6:00 a. m., 11:00a.m., 6:00 PM y 18:00.
TIME_REGEX = re.compile(
    r"(?<![\d:])(?P<hour>\d{1,2})\s*:\s*(?P<minute>[0-5]\d)"
    r"(?:\s*(?P<ampm>[aApP])\s*\.?\s*[mM]\s*\.?)?(?!\d)"
)

MAX_DIMENSION = 1600  # Límite para acelerar OCR y cuidar la RAM sin perder nitidez


@dataclass
class TimeCandidate:
    source_text: str
    time: str
    confidence: float
    x: int
    y: int
    width: int
    height: int
    relative_x: float
    relative_y: float
    has_meridiem: bool


def parse_clock(text: str) -> tuple[str, bool] | None:
    """Retorna hora HH:MM en formato 24h, conservando incertidumbre AM/PM."""
    match = TIME_REGEX.search(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    ap = match.group("ampm")
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap.lower() == "p" else 0)
    elif hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}", bool(ap)


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Convierte a escala de grises, detecta modo oscuro y mejora el contraste."""
    gray = ImageOps.grayscale(image)
    hist = gray.histogram()
    dark_pixels = sum(hist[:128])
    light_pixels = sum(hist[128:])
    # Si predominan los píxeles oscuros (Modo Oscuro), invertir ayuda a Tesseract
    if dark_pixels > light_pixels * 1.4:
        gray = ImageOps.invert(gray)
    return ImageOps.autocontrast(gray, cutoff=1)


def collect_candidates(data: dict[str, Any], width: int, height: int) -> list[TimeCandidate]:
    """Reconstruye líneas OCR y busca horarios con sus coordenadas."""
    lines: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for i, token in enumerate(data["text"]):
        if not token or not token.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append({
            "text": token.strip(),
            "x": int(data["left"][i]),
            "y": int(data["top"][i]),
            "w": int(data["width"][i]),
            "h": int(data["height"][i]),
            "conf": max(0.0, float(data["conf"][i])),
        })
    output: list[TimeCandidate] = []
    for tokens in lines.values():
        tokens.sort(key=lambda x: x["x"])
        line = " ".join(t["text"] for t in tokens)
        for m in TIME_REGEX.finditer(line):
            parsed = parse_clock(m.group())
            if parsed is None:
                continue
            # Mapear caracteres de texto reconstruido a los tokens que los contienen.
            starts: list[tuple[int, int, dict[str, Any]]] = []
            offset = 0
            for token in tokens:
                starts.append((offset, offset + len(token["text"]), token))
                offset += len(token["text"]) + 1
            used = [tok for s, e, tok in starts if s < m.end() and e > m.start()]
            if not used:
                continue
            x = min(t["x"] for t in used)
            y = min(t["y"] for t in used)
            x_right = max(t["x"] + t["w"] for t in used)
            y_bottom = max(t["y"] + t["h"] for t in used)
            output.append(TimeCandidate(
                source_text=m.group().strip(),
                time=parsed[0],
                confidence=round(sum(t["conf"] for t in used) / len(used), 1),
                x=x,
                y=y,
                width=x_right - x,
                height=y_bottom - y,
                relative_x=round(x / width, 4),
                relative_y=round(y / height, 4),
                has_meridiem=parsed[1],
            ))
    return output


def analyze_image(image_bytes: bytes) -> dict[str, Any]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("La imagen no pudo leerse. Utiliza un JPG, PNG o WEBP válido.") from exc

    orig_w, orig_h = image.size
    if orig_w < 180 or orig_h < 180:
        raise ValueError("La imagen es demasiado pequeña para reconocer una hora.")
    if orig_w * orig_h > 24_000_000:
        raise ValueError("La imagen supera el tamaño máximo permitido (24 megapíxeles).")

    # Redimensionar si es muy grande para acelerar OCR y ahorrar RAM en sesión
    if max(orig_w, orig_h) > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    width, height = image.size

    # Guardar versión comprimida para la vista previa de Streamlit (alineada con las coordenadas)
    preview_buf = io.BytesIO()
    image.save(preview_buf, format="JPEG", quality=80)
    preview_bytes = preview_buf.getvalue()

    processed_img = preprocess_for_ocr(image)

    try:
        try:
            data = pytesseract.image_to_data(
                processed_img,
                lang="spa+eng",
                config="--psm 11",
                output_type=pytesseract.Output.DICT,
                timeout=22,
            )
        except pytesseract.TesseractError:
            # Fallback automático si el idioma 'spa' no está instalado en la máquina local
            data = pytesseract.image_to_data(
                processed_img,
                config="--psm 11",
                output_type=pytesseract.Output.DICT,
                timeout=22,
            )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("Tesseract OCR no está instalado en este equipo.") from exc
    except RuntimeError as exc:
        raise RuntimeError("El motor OCR no pudo terminar el análisis a tiempo.") from exc

    candidates = collect_candidates(data, width, height)
    # El área superior (12%) suele contener la hora del teléfono.
    # La parte inferior (último 3.5%) acostumbra a ser la barra para redactar.
    plausible = [c for c in candidates if 0.12 <= c.relative_y <= 0.965]
    if not plausible:
        return {
            "status": "sin_hora",
            "time": None,
            "observations": "No se encontró una marca horaria legible en el área de conversación.",
            "selected": None,
            "candidates": [asdict(c) for c in candidates],
            "size": {"width": width, "height": height},
            "preview_bytes": preview_bytes,
        }

    # Regla del proyecto: seleccionar el último horario VISUALMENTE (más abajo en Y, y más a la derecha en X)
    selected = max(plausible, key=lambda c: (c.y + c.height, c.x))

    review_reasons: list[str] = []
    if selected.confidence < 72:
        review_reasons.append("Reconocimiento OCR de baja confianza")
    if selected.relative_x < 0.18:
        review_reasons.append("La hora está muy pegada al borde izquierdo (posible texto del chat o duración de audio)")
    if selected.relative_y < 0.35:
        review_reasons.append("La hora seleccionada está bastante arriba en la conversación")
    if not selected.has_meridiem and selected.time.startswith("00:"):
        review_reasons.append("Posible duración de audio/video (00:MM) o medianoche en formato 12h sin AM/PM")
    elif not selected.has_meridiem and int(selected.source_text.split(":")[0]) <= 12:
        review_reasons.append("No se distingue AM/PM; verifica si es mañana o tarde")

    return {
        "status": "revision" if review_reasons else "detectada",
        "time": selected.time,
        "observations": "; ".join(review_reasons)
        if review_reasons
        else "Hora del último mensaje visible sugerida por OCR; comprobar en caso de dudas.",
        "selected": asdict(selected),
        "candidates": [asdict(c) for c in sorted(candidates, key=lambda c: c.y)],
        "size": {"width": width, "height": height},
        "preview_bytes": preview_bytes,
    }
