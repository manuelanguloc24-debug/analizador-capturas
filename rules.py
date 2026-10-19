"""Reglas de negocio independientes de Streamlit."""
import re
from datetime import time

DEPARTAMENTOS = [
    "Ahuachapán", "Cabañas", "Chalatenango", "Cuscatlán", "La Libertad",
    "La Paz", "La Unión", "Morazán", "San Miguel", "San Salvador",
    "San Vicente", "Santa Ana", "Sonsonate", "Usulután",
]
HORA_INICIO = time(6, 0)
HORA_INICIO_TEXTO = "06:00"


def is_valid_hhmm(val: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", val.strip()))


def evaluate_punctuality(hhmm: str, tolerance: int) -> tuple[int | None, str]:
    """Evalúa contra 06:00; las horas OCR no incluyen segundos verificables."""
    if not hhmm or not is_valid_hhmm(hhmm):
        return None, "Pendiente"
    hours, minutes = map(int, hhmm.split(":"))
    delta = hours * 60 + minutes - (HORA_INICIO.hour * 60 + HORA_INICIO.minute)
    if delta < 0:
        return delta, "Antes de inicio"
    return delta, "En tiempo" if delta <= tolerance else "Tardía"
