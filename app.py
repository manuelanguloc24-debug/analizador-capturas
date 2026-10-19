"""Analizador de capturas de WhatsApp · MVP 0.5.

Funciones: OCR último horario visible; 06:00 hora de referencia fija;
navegación por los 14 departamentos; eliminación de duplicados; historial
permanente en Supabase y retención de imágenes por 60 días.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps

from cloud_store import CloudStore
from ocr_engine import analyze_image
from rules import DEPARTAMENTOS, HORA_INICIO_TEXTO, evaluate_punctuality, is_valid_hhmm

st.set_page_config(
    page_title="Analizador de capturas | MVP 0.5",
    page_icon="🕒",
    layout="wide",
    initial_sidebar_state="expanded",
)

ESTADOS = ["detectada", "corregida", "revision", "sin_hora", "duplicada", "error"]
TABLE_COLUMNS = [
    "id", "fecha_actividad", "departamento", "archivo", "hora_ocr",
    "hora_final", "hora_meta", "diferencia_min", "cumplimiento",
    "estado", "observaciones", "hash_sha256",
]
DISPLAY_COLUMNS = [
    "id", "fecha_actividad", "departamento", "archivo", "hora_ocr",
    "hora_final", "diferencia_min", "cumplimiento", "estado", "observaciones",
]

st.markdown("""
<style>
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
div[data-testid="stMetric"] { background: var(--secondary-background-color); padding: 12px; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)


def secret(name: str) -> str:
    """Leer secrets de Streamlit Cloud o variable de entorno local."""
    try:
        value = st.secrets.get(name)
        if value:
            return str(value).strip()
    except (FileNotFoundError, KeyError, AttributeError):
        pass
    return os.environ.get(name, "").strip()


def cloud_client(url: str, key: str) -> CloudStore:
    return CloudStore(url, key)


def safe_image_preview(
    raw: bytes,
    selection: dict[str, Any] | None,
    ocr_size: dict[str, int] | None = None,
) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    if selection and ocr_size:
        img = img.resize((int(ocr_size["width"]), int(ocr_size["height"])))
    if selection:
        x, y = selection["x"], selection["y"]
        w, h = selection["width"], selection["height"]
        drawer = ImageDraw.Draw(img)
        drawer.rectangle(
            [max(0, x - 6), max(0, y - 6), x + w + 6, y + h + 6],
            outline="#16a34a",
            width=4,
        )
    return img


def export_data(rows: list[dict[str, Any]]) -> bytes:
    frame = pd.DataFrame([{col: row.get(col, "") for col in TABLE_COLUMNS} for row in rows])
    return frame.to_csv(index=False).encode("utf-8-sig")


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {col: row.get(col) for col in TABLE_COLUMNS}


# Modo nube: requiere clave privada de servidor y contraseña.
url, service_key, admin_password = (
    secret("SUPABASE_URL"),
    secret("SUPABASE_SERVICE_ROLE_KEY"),
    secret("ADMIN_PASSWORD"),
)
cloud_configured = bool(url and service_key)
if cloud_configured and not admin_password:
    st.error("Configuración incompleta: falta ADMIN_PASSWORD. Se bloqueó el acceso a datos de la nube.")
    st.stop()

if cloud_configured:
    if not st.session_state.get("autorizado", False):
        st.title("🔒 Acceso administrativo")
        entered = st.text_input("Contraseña", type="password")
        if st.button("Ingresar", type="primary"):
            if hmac.compare_digest(entered, admin_password):
                st.session_state.autorizado = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")
        st.stop()
    try:
        store = cloud_client(url, service_key)
    except Exception as exc:
        st.error(f"No fue posible iniciar Supabase: {type(exc).__name__}: {exc}")
        st.stop()
else:
    store = None
    st.warning(
        "Modo demostración sin nube: los resultados solo permanecerán en esta sesión. "
        "Configura los secretos de Supabase para activar el historial permanente."
    )

if "registros_locales" not in st.session_state:
    st.session_state.registros_locales = []

# Ejecución automática de limpieza de imágenes vencidas (>60 días) una vez por sesión
if store and not st.session_state.get("limpieza_ejecutada", False):
    try:
        eliminadas, _ = store.cleanup_expired_images(batch_size=50)
        st.session_state.limpieza_ejecutada = True
        if eliminadas > 0:
            st.toast(f"🧹 Se eliminaron automáticamente {eliminadas} imágenes con más de 60 días.")
    except Exception:
        pass

st.title("Analizador de capturas de WhatsApp")
st.caption("MVP 0.5 · Hora oficial 06:00 a. m. · 14 departamentos · Historial y retención de 60 días")
st.info(
    "El OCR propone la hora del **último mensaje visible**. No utiliza metadatos de imagen. "
    "Las capturas no prueban por sí mismas la autenticidad del horario; revisa los casos dudosos."
)

with st.sidebar:
    st.header("Navegación")
    nav_dep = st.selectbox("Departamento que deseas consultar", ["Todos"] + DEPARTAMENTOS, key="nav_dep")
    st.caption("Cambiar de departamento NO elimina ni reinicia capturas de otros departamentos.")
    st.divider()
    st.subheader("Configuración de la actividad")
    activity_date = st.date_input("Fecha de actividad", value=date.today(), key="activity_date")
    st.text_input("Hora establecida (fija)", value="06:00 a. m.", disabled=True)
    tolerance = st.number_input(
        "Minutos de tolerancia para 'En tiempo'",
        min_value=0,
        max_value=720,
        value=0,
        step=1,
        key="tolerance",
    )
    st.caption("La hora oficial no cambia. La tolerancia solamente afecta las etiquetas de cumplimiento.")
    st.divider()
    st.caption("🟢 Nube activa (Supabase)" if store else "🟠 Modo sesión temporal")
    if store and st.button("Cerrar sesión", use_container_width=True):
        st.session_state.autorizado = False
        st.rerun()


def fetch_rows(
    department: str = "Todos",
    start: date | None = None,
    end: date | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if store:
        return store.list_records(
            department,
            start.isoformat() if start else None,
            end.isoformat() if end else None,
            limit,
            offset,
        )
    rows = [
        row
        for row in st.session_state.registros_locales
        if (department == "Todos" or row["departamento"] == department)
        and (start is None or str(row["fecha_actividad"]) >= start.isoformat())
        and (end is None or str(row["fecha_actividad"]) <= end.isoformat())
    ]
    return list(reversed(rows))[offset : offset + limit]


def save_record(row: dict[str, Any], raw: bytes | None, mime: str) -> dict[str, Any]:
    if store:
        return store.save_record(row, raw, mime)
    row = dict(row, id=str(uuid4()), _preview_bytes=raw)
    st.session_state.registros_locales.append(row)
    return row


def save_correction(row: dict[str, Any], payload: dict[str, Any]) -> None:
    if store:
        store.update_record(str(row["id"]), payload)
        return
    for current in st.session_state.registros_locales:
        if current["id"] == row["id"]:
            current.update(payload)
            return
    raise ValueError("Registro no encontrado")


def delete_record(row: dict[str, Any]) -> None:
    if store:
        store.delete_record(str(row["id"]))
        return
    st.session_state.registros_locales = [
        r for r in st.session_state.registros_locales if r["id"] != row["id"]
    ]


def duplicates_in_date(sha: str, date_str: str) -> bool:
    if store:
        return store.has_original(date_str, sha)
    return any(
        row["fecha_actividad"] == date_str
        and row["hash_sha256"] == sha
        and row["estado"] != "duplicada"
        for row in st.session_state.registros_locales
    )


upload_tab, result_tab, panels_tab, history_tab = st.tabs([
    "📤 Procesar capturas",
    "📋 Revisar resultados",
    "📊 Paneles",
    "🗂️ Historial",
])

with upload_tab:
    st.subheader("Importación masiva de capturas")
    if nav_dep == "Todos":
        upload_dep = st.selectbox("Departamento al que se asignará este lote", DEPARTAMENTOS)
    else:
        upload_dep = nav_dep
        st.success(f"El lote se clasificará en: **{upload_dep}**")
    files = st.file_uploader(
        "Imágenes JPG, PNG o WEBP (hasta 100 por lote)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="uploader",
    )
    if files:
        st.caption(f"{len(files)} imágenes seleccionadas · {upload_dep} · {activity_date:%d/%m/%Y}")
    if st.button("Analizar y guardar", type="primary", disabled=not files):
        if len(files) > 100:
            st.error("Máximo 100 imágenes por lote.")
        else:
            bar = st.progress(0.0)
            ok, duplicate_count, failures = 0, 0, []
            scanned: set[str] = set()
            for idx, uploaded in enumerate(files):
                raw = uploaded.getvalue()
                sha = hashlib.sha256(raw).hexdigest()
                date_str = activity_date.isoformat()
                try:
                    repeated = sha in scanned or duplicates_in_date(sha, date_str)
                except Exception as db_exc:
                    failures.append(f"{uploaded.name} (Error consultando BD): {db_exc}")
                    bar.progress((idx + 1) / len(files), text=f"Procesando {idx + 1}/{len(files)}")
                    continue
                scanned.add(sha)
                analyzed: dict[str, Any] = {
                    "status": "error",
                    "time": None,
                    "observations": "",
                    "selected": None,
                    "preview_bytes": raw,
                }
                if repeated:
                    analyzed.update(
                        status="duplicada",
                        observations="Archivo idéntico a uno previamente registrado para esta fecha.",
                    )
                    duplicate_count += 1
                elif len(raw) > 15 * 1024 * 1024:
                    analyzed["observations"] = "Archivo mayor a 15 MB; OCR omitido."
                else:
                    try:
                        analyzed = analyze_image(raw)
                    except (ValueError, RuntimeError, OSError) as exc:
                        analyzed["observations"] = str(exc)

                extracted = analyzed.get("time") or ""
                diff, status = evaluate_punctuality(extracted, int(tolerance))
                row = {
                    "fecha_actividad": date_str,
                    "departamento": upload_dep,
                    "archivo": uploaded.name,
                    "hash_sha256": sha,
                    "hora_ocr": extracted or None,
                    "hora_final": extracted or None,
                    "hora_meta": HORA_INICIO_TEXTO,
                    "diferencia_min": diff,
                    "cumplimiento": status,
                    "estado": analyzed.get("status", "error"),
                    "observaciones": analyzed.get("observations", ""),
                    "ocr_selected": analyzed.get("selected"),
                    "ocr_size": analyzed.get("size"),
                }
                optimized_bytes = analyzed.get("preview_bytes", raw)
                mime = "image/jpeg" if "preview_bytes" in analyzed else (
                    {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
                    .get(uploaded.name.rsplit(".", 1)[-1].lower(), "image/jpeg")
                )
                image_bytes = None if repeated or len(raw) > 15 * 1024 * 1024 else optimized_bytes
                try:
                    saved = save_record(row, image_bytes, mime)
                    if not store:
                        saved["_preview_bytes"] = optimized_bytes
                        saved["_selected"] = analyzed.get("selected")
                    ok += 1
                except Exception as exc:
                    failures.append(f"{uploaded.name}: {type(exc).__name__}: {exc}")
                bar.progress((idx + 1) / len(files), text=f"Procesando {idx + 1}/{len(files)}")
            st.success(f"Registros guardados: {ok} · duplicados detectados: {duplicate_count}")
            if failures:
                st.error(f"No se guardaron {len(failures)} archivos. Abre el detalle abajo para ver la causa exacta:")
                with st.expander("Ver detalle del error de Supabase", expanded=True):
                    for err in failures:
                        st.code(err)
            st.caption("Los resultados aparecen al abrir 'Revisar resultados' y 'Historial'.")

with result_tab:
    st.subheader("Resultados y correcciones")
    st.caption(f"Vista: {nav_dep} · se muestran hasta 500 registros recientes del departamento seleccionado.")
    try:
        rows = fetch_rows(nav_dep, limit=500)
    except Exception as exc:
        st.error(f"No fue posible consultar resultados: {type(exc).__name__}: {exc}")
        rows = []
    if not rows:
        st.info("No hay registros para este departamento. Cambia la selección en el menú lateral o sube imágenes.")
    else:
        frame = pd.DataFrame([{key: row.get(key) for key in DISPLAY_COLUMNS} for row in rows])
        editable = st.data_editor(
            frame,
            hide_index=True,
            use_container_width=True,
            disabled=["id", "fecha_actividad", "archivo", "hora_ocr", "diferencia_min", "cumplimiento"],
            column_config={
                "departamento": st.column_config.SelectboxColumn("Departamento", options=DEPARTAMENTOS, required=True),
                "hora_final": st.column_config.TextColumn("Hora final (HH:MM)"),
                "estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS, required=True),
                "id": st.column_config.TextColumn("ID", width="small"),
            },
            key=f"editor_{nav_dep}_{len(rows)}",
        )
        c_save, c_csv = st.columns([1, 1])
        with c_save:
            if st.button("💾 Guardar correcciones", type="primary", use_container_width=True):
                changes = []
                errors = []
                by_id = {str(row["id"]): row for row in rows}
                for _, item in editable.iterrows():
                    orig = by_id[str(item["id"])]
                    new_hour = "" if pd.isna(item["hora_final"]) else str(item["hora_final"]).strip()
                    dep = str(item["departamento"])
                    if new_hour and not is_valid_hhmm(new_hour):
                        errors.append(f"{item['archivo']}: hora inválida {new_hour!r}")
                        continue
                    if dep not in DEPARTAMENTOS:
                        errors.append(f"{item['archivo']}: departamento inválido")
                        continue
                    current_time = orig.get("hora_final") or ""
                    state = str(item["estado"])
                    if new_hour != current_time:
                        state = "corregida" if new_hour else "sin_hora"
                    diff, label = evaluate_punctuality(new_hour, int(tolerance))
                    payload = {
                        "departamento": dep,
                        "hora_final": new_hour or None,
                        "estado": state,
                        "observaciones": "" if pd.isna(item["observaciones"]) else str(item["observaciones"]),
                        "hora_meta": HORA_INICIO_TEXTO,
                        "diferencia_min": diff,
                        "cumplimiento": label,
                    }
                    if any(payload[k] != orig.get(k) for k in payload):
                        changes.append((orig, payload))
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    saved_n = 0
                    for orig, payload in changes:
                        try:
                            save_correction(orig, payload)
                            saved_n += 1
                        except Exception as exc:
                            st.error(f"Error guardando {orig['archivo']}: {exc}")
                    st.success(f"Correcciones guardadas: {saved_n}")
                    if saved_n:
                        st.rerun()
        with c_csv:
            st.download_button(
                "📥 Descargar CSV visible",
                export_data(rows),
                "capturas_resultados.csv",
                "text/csv",
                use_container_width=True,
            )

        st.divider()
        st.markdown("#### 🗑️ Duplicados detectados")
        duplicated = [row for row in rows if row.get("estado") == "duplicada"]
        if not duplicated:
            st.caption("No se detectaron duplicados en los registros mostrados.")
        else:
            st.warning(f"Duplicados encontrados: {len(duplicated)}. Solo se borra el duplicado seleccionado, no el original.")
            for dup in duplicated[:50]:
                left, right = st.columns([4, 1])
                left.write(f"{dup['archivo']} · {dup['departamento']} · {dup['fecha_actividad']}")
                if right.button("Eliminar", key=f"delete_{dup['id']}", use_container_width=True):
                    try:
                        delete_record(dup)
                        st.success("Duplicado eliminado")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"No se pudo eliminar: {exc}")
            if len(duplicated) > 50:
                st.caption("Para mostrar otros duplicados, usa los filtros del historial.")

        st.divider()
        st.markdown("#### 🔍 Inspección individual")
        options = {f"{r['archivo']} · {r['departamento']} · {str(r['id'])[:8]}": r for r in rows}
        chosen = st.selectbox("Selecciona un registro", list(options), key=f"inspect_{nav_dep}")
        chosen_row = options[chosen]
        left, right = st.columns([1, 1])
        with left:
            try:
                raw = store.image_bytes(chosen_row) if store else chosen_row.get("_preview_bytes")
                if raw:
                    sel = chosen_row.get("ocr_selected") or chosen_row.get("_selected")
                    st.image(
                        safe_image_preview(raw, sel, chosen_row.get("ocr_size")),
                        use_container_width=True,
                    )
                else:
                    st.info("Imagen no disponible: duplicada, no almacenada o vencida después de 60 días.")
            except Exception as exc:
                st.warning(f"No fue posible recuperar la imagen: {type(exc).__name__}")
        with right:
            st.write(f"**Hora detectada:** {chosen_row.get('hora_ocr') or 'No identificada'}")
            st.write(f"**Hora final:** {chosen_row.get('hora_final') or 'Pendiente'}")
            st.write(f"**Estado:** {chosen_row.get('estado')}")
            st.write(f"**Observaciones:** {chosen_row.get('observaciones') or '—'}")
            if chosen_row.get("imagen_expira_en"):
                st.caption(f"La imagen original vence el {str(chosen_row['imagen_expira_en'])[:10]}; el registro permanece.")

with panels_tab:
    st.subheader("Indicadores por departamento")
    st.caption("Estos cálculos corresponden a los registros cargados en la vista (máximo 500).")
    try:
        panel_rows = fetch_rows(nav_dep, limit=500)
    except Exception as exc:
        st.error(f"No se pudieron consultar indicadores: {exc}")
        panel_rows = []
    if panel_rows:
        df = pd.DataFrame(panel_rows)
        accepted = df[~df["estado"].isin(["duplicada", "error", "sin_hora", "revision"])]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Registros visibles", len(df))
        c2.metric("En tiempo (verificados)", int((accepted["cumplimiento"] == "En tiempo").sum()))
        c3.metric("Pendientes de revisión", int(df["estado"].isin(["revision", "sin_hora", "error"]).sum()))
        c4.metric("Duplicados", int((df["estado"] == "duplicada").sum()))
        summary = (
            df.groupby("departamento", dropna=False)
            .agg(
                Capturas=("id", "count"),
                En_tiempo=("cumplimiento", lambda s: int((s == "En tiempo").sum())),
                Duplicadas=("estado", lambda s: int((s == "duplicada").sum())),
            )
            .reset_index()
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)
    else:
        st.info("No hay datos para mostrar.")

with history_tab:
    st.subheader("Historial de capturas")
    st.caption("Los registros se conservan en la base de datos. Las imágenes originales se eliminan después de 60 días.")
    f1, f2 = st.columns(2)
    default_start = date.today() - timedelta(days=30)
    with f1:
        start = st.date_input("Desde", value=default_start, key="hist_start")
    with f2:
        end = st.date_input("Hasta", value=date.today(), key="hist_end")
    if start > end:
        st.error("La fecha inicial no puede ser mayor que la final.")
    else:
        page = st.number_input(
            "Página de resultados (500 registros por página)",
            min_value=1,
            value=1,
            step=1,
            key="hist_page",
        )
        try:
            history = fetch_rows(nav_dep, start, end, 500, (int(page) - 1) * 500)
            if not history:
                st.info("No hay registros en el período y departamento seleccionados.")
            else:
                st.dataframe(
                    pd.DataFrame([clean_row(row) for row in history]),
                    use_container_width=True,
                    hide_index=True,
                )
                st.download_button(
                    "📥 Exportar página actual (CSV)",
                    export_data(history),
                    file_name=f"historial_{start}_{end}_pag{page}.csv",
                    mime="text/csv",
                )
                if len(history) == 500:
                    st.caption("Puede haber más registros. Aumenta el número de página para consultarlos.")
        except Exception as exc:
            st.error(f"No se pudo abrir el historial: {exc}")

st.divider()
st.caption("Historial en nube activo con Supabase · Retención automática de imágenes por 60 días.")