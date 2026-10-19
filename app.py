"""Prototipo Streamlit para revisar marcas horarias en capturas de WhatsApp.

La hora propuesta es la del último mensaje visualmente, no el tiempo EXIF,
la fecha del archivo, ni la hora de carga. Incluye catálogo territorial completo,
cálculo de puntualidad y edición directa en tabla.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, time
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageOps

from ocr_engine import analyze_image

st.set_page_config(
    page_title="Analizador de capturas | MVP 0.4",
    page_icon="🕒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Catálogo territorial completo de El Salvador (14 Departamentos -> 44 Municipios -> 262 Distritos)
TERRITORIO_SV: dict[str, dict[str, list[str]]] = {
    "Ahuachapán": {
        "Ahuachapán Norte": ["Atiquizaya", "El Refugio", "San Lorenzo", "Turín"],
        "Ahuachapán Centro": ["Ahuachapán", "Apaneca", "Concepción de Ataco", "Tacuba"],
        "Ahuachapán Sur": ["Guaymango", "Jujutla", "San Francisco Menéndez", "San Pedro Puxtla"],
    },
    "Cabañas": {
        "Cabañas Este": ["Dolores", "Guacotecti", "San Isidro", "Sensuntepeque", "Victoria"],
        "Cabañas Oeste": ["Cinquera", "Ilobasco", "Jutiapa", "Tejutepeque"],
    },
    "Chalatenango": {
        "Chalatenango Norte": ["Citalá", "La Palma", "San Ignacio"],
        "Chalatenango Centro": [
            "Agua Caliente", "Dulce Nombre de María", "El Paraíso", "La Reina",
            "Nueva Concepción", "San Fernando", "San Francisco Morazán",
            "San Rafael", "Santa Rita", "Tejutla",
        ],
        "Chalatenango Sur": [
            "Arcatao", "Azacualpa", "Cancasque", "Chalatenango", "Comalapa",
            "Concepción Quezaltepeque", "El Carrizal", "La Laguna", "Las Vueltas",
            "Nombre de Jesús", "Nueva Trinidad", "Ojos de Agua", "Potonico",
            "San Antonio de la Cruz", "San Antonio Los Ranchos", "San Francisco Lempa",
            "San Isidro Labrador", "San José Las Flores", "San Luis del Carmen",
            "San Miguel de Mercedes",
        ],
    },
    "Cuscatlán": {
        "Cuscatlán Norte": [
            "Oratorio de Concepción", "San Bartolomé Perulapía",
            "San José Guayabal", "San Pedro Perulapán", "Suchitoto",
        ],
        "Cuscatlán Sur": [
            "Candelaria", "Cojutepeque", "El Carmen", "El Rosario",
            "Monte San Juan", "San Cristóbal", "San Rafael Cedros",
            "San Ramón", "Santa Cruz Analquito", "Santa Cruz Michapa", "Tenancingo",
        ],
    },
    "La Libertad": {
        "La Libertad Norte": ["Quezaltepeque", "San Matías", "San Pablo Tacachico"],
        "La Libertad Centro": ["Ciudad Arce", "San Juan Opico"],
        "La Libertad Oeste": ["Colón", "Jayaque", "Sacacoyo", "Talnique", "Tepecoyo"],
        "La Libertad Este": [
            "Antiguo Cuscatlán", "Huizúcar", "Nuevo Cuscatlán",
            "San José Villanueva", "Zaragoza",
        ],
        "La Libertad Costa": ["Chiltiupán", "Jicalapa", "La Libertad", "Tamanique", "Teotepeque"],
        "La Libertad Sur": ["Comasagua", "Santa Tecla"],
    },
    "La Paz": {
        "La Paz Oeste": [
            "Cuyultitán", "Olocuilta", "San Francisco Chinameca",
            "San Juan Talpa", "San Luis Talpa", "San Pedro Masahuat", "Tapalhuaca",
        ],
        "La Paz Centro": [
            "El Rosario", "Jerusalén", "Mercedes La Ceiba", "Paraíso de Osorio",
            "San Antonio Masahuat", "San Emigdio", "San Juan Tepezontes",
            "San Luis La Herradura", "San Miguel Tepezontes", "San Pedro Nonualco",
            "Santa María Ostuma", "Santiago Nonualco",
        ],
        "La Paz Este": ["San Juan Nonualco", "San Rafael Obrajuelo", "Zacatecoluca"],
    },
    "La Unión": {
        "La Unión Norte": [
            "Anamorós", "Bolívar", "Concepción de Oriente", "El Sauce", "Lislique",
            "Nueva Esparta", "Pasaquina", "Polorós", "San José", "Santa Rosa de Lima",
        ],
        "La Unión Sur": [
            "Conchagua", "El Carmen", "Intipucá", "La Unión",
            "Meanguera del Golfo", "San Alejo", "Yayantique", "Yucuaiquín",
        ],
    },
    "Morazán": {
        "Morazán Norte": [
            "Arambala", "Cacaopera", "Corinto", "El Rosario", "Joateca",
            "Jocoaitique", "Meanguera", "Perquín", "San Fernando",
            "San Isidro", "Torola",
        ],
        "Morazán Sur": [
            "Chilanga", "Delicias de Concepción", "El Divisadero", "Gualococti",
            "Guatajiagua", "Jocoro", "Lolotiquillo", "Osicala", "San Carlos",
            "San Francisco Gotera", "San Simón", "Sensembra", "Sociedad",
            "Yamabal", "Yoloaiquín",
        ],
    },
    "San Miguel": {
        "San Miguel Norte": [
            "Carolina", "Chapeltique", "Ciudad Barrios", "Nuevo Edén de San Juan",
            "San Antonio", "San Gerardo", "San Luis de la Reina", "Sesori",
        ],
        "San Miguel Centro": [
            "Chirilagua", "Comacarán", "Moncagua", "Quelepa", "San Miguel", "Uluazapa",
        ],
        "San Miguel Oeste": [
            "Chinameca", "El Tránsito", "Lolotique", "Nueva Guadalupe",
            "San Jorge", "San Rafael Oriente",
        ],
    },
    "San Salvador": {
        "San Salvador Norte": ["Aguilares", "El Paisnal", "Guazapa"],
        "San Salvador Oeste": ["Apopa", "Nejapa"],
        "San Salvador Este": ["Ilopango", "San Martín", "Soyapango", "Tonacatepeque"],
        "San Salvador Centro": [
            "Ayutuxtepeque", "Cuscatancingo", "Delgado", "Mejicanos", "San Salvador",
        ],
        "San Salvador Sur": [
            "Panchimalco", "Rosario de Mora", "San Marcos", "Santiago Texacuangos", "Santo Tomás",
        ],
    },
    "San Vicente": {
        "San Vicente Norte": [
            "Apastepeque", "Santa Clara", "San Esteban Catarina", "San Ildefonso",
            "San Lorenzo", "San Sebastián", "Santo Domingo",
        ],
        "San Vicente Sur": [
            "Guadalupe", "San Cayetano Istepeque", "San Vicente",
            "Tecoluca", "Tepetitán", "Verapaz",
        ],
    },
    "Santa Ana": {
        "Santa Ana Norte": ["Masahuat", "Metapán", "Santa Rosa Guachipilín", "Texistepeque"],
        "Santa Ana Centro": ["Santa Ana"],
        "Santa Ana Este": ["Coatepeque", "El Congo"],
        "Santa Ana Oeste": [
            "Candelaria de la Frontera", "Chalchuapa", "El Porvenir",
            "San Antonio Pajonal", "San Sebastián Salitrillo", "Santiago de la Frontera",
        ],
    },
    "Sonsonate": {
        "Sonsonate Norte": ["Juayúa", "Nahuizalco", "Salcoatitán", "Santa Catarina Masahuat"],
        "Sonsonate Centro": [
            "Nahulingo", "San Antonio del Monte", "Santo Domingo de Guzmán",
            "Sonsonate", "Sonzacate",
        ],
        "Sonsonate Este": [
            "Armenia", "Caluco", "Cuisnahuat", "Izalco", "San Julián", "Santa Isabel Ishuatán",
        ],
        "Sonsonate Oeste": ["Acajutla"],
    },
    "Usulután": {
        "Usulután Norte": [
            "Alegría", "Berlín", "El Triunfo", "Estanzuelas", "Jucuapa",
            "Mercedes Umaña", "Nueva Granada", "San Buenaventura", "Santiago de María",
        ],
        "Usulután Este": [
            "California", "Concepción Batres", "Ereguayquín", "Jucuarán",
            "Ozatlán", "San Dionisio", "Santa Elena", "Santa María", "Tecapán", "Usulután",
        ],
        "Usulután Oeste": ["Jiquilisco", "Puerto El Triunfo", "San Agustín", "San Francisco Javier"],
    },
}

DEPARTAMENTOS = list(TERRITORIO_SV.keys())

COLUMNS = [
    "id", "fecha_actividad", "departamento", "municipio", "distrito",
    "archivo", "hora_ocr", "hora_final", "hora_meta", "diferencia_min",
    "cumplimiento", "estado", "observaciones", "hash_corto",
]

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
div[data-testid="stMetric"] {background: var(--secondary-background-color); padding: 14px; border-radius: 11px;}
</style>
""", unsafe_allow_html=True)

if "registros" not in st.session_state:
    st.session_state.registros = []


def is_valid_hhmm(val: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", val.strip()))


def evaluate_punctuality(
    hora_str: str,
    use_cutoff: bool,
    cutoff_time: time,
    tolerance_min: int,
) -> tuple[str, int | None, str]:
    if not use_cutoff:
        return "", None, "No aplica"
    meta_str = cutoff_time.strftime("%H:%M")
    if not hora_str or not is_valid_hhmm(hora_str):
        return meta_str, None, "Pendiente"
    h, m = map(int, hora_str.strip().split(":"))
    msg_minutes = h * 60 + m
    cutoff_minutes = cutoff_time.hour * 60 + cutoff_time.minute
    diff = msg_minutes - cutoff_minutes
    if diff < 0:
        return meta_str, diff, "Antes de inicio"
    if diff <= tolerance_min:
        return meta_str, diff, "En tiempo"
    return meta_str, diff, "Tardía"


def table_rows() -> list[dict[str, Any]]:
    return [{k: item.get(k, "") for k in COLUMNS} for item in st.session_state.registros]


def export_csv() -> bytes:
    frame = pd.DataFrame(table_rows(), columns=COLUMNS)
    return frame.to_csv(index=False).encode("utf-8-sig")


def annotated_preview(data: bytes, selected: dict[str, Any] | None) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if selected:
        x, y = selected["x"], selected["y"]
        w, h = selected["width"], selected["height"]
        drawer = ImageDraw.Draw(img)
        drawer.rectangle((max(0, x - 8), max(0, y - 8), x + w + 8, y + h + 8), outline="#16a34a", width=6)
    return img


def status_tag(value: str) -> str:
    return {
        "detectada": "Hora sugerida",
        "corregida": "Corregida",
        "revision": "Revisar hora",
        "sin_hora": "Sin hora",
        "duplicada": "Duplicada",
        "error": "Error",
    }.get(value, value)


st.title("Analizador de capturas de WhatsApp")
st.caption("MVP 0.4 · OCR del último mensaje visible · Control de puntualidad y catálogo territorial")
st.info(
    "Regla principal: la hora que se propone procede del **último mensaje visualmente** "
    "en la captura, no de los metadatos ni de la barra superior del teléfono. "
    "Este sistema no certifica la autenticidad de las capturas."
)

with st.sidebar:
    st.header("Configuración del lote")
    activity_date = st.date_input("Fecha de la actividad", value=date.today())

    departamento = st.selectbox("Departamento", ["Sin clasificar"] + DEPARTAMENTOS)
    if departamento != "Sin clasificar":
        municipios_disp = list(TERRITORIO_SV[departamento].keys())
        municipio = st.selectbox("Municipio", ["Sin clasificar"] + municipios_disp)
    else:
        municipio = "Sin clasificar"

    if departamento != "Sin clasificar" and municipio != "Sin clasificar":
        distritos_disp = TERRITORIO_SV[departamento][municipio]
        distrito = st.selectbox("Distrito", ["Sin clasificar"] + distritos_disp)
    else:
        distrito = "Sin clasificar"

    st.divider()
    st.subheader("Métrica de puntualidad")
    use_cutoff = st.checkbox("Evaluar hora de cumplimiento", value=True)
    cutoff_time = st.time_input("Hora de inicio / instrucción", value=time(18, 0), disabled=not use_cutoff)
    tolerance_min = st.number_input(
        "Tolerancia para 'En tiempo' (minutos)",
        min_value=0, max_value=720, value=30, step=5,
        disabled=not use_cutoff,
    )
    if st.session_state.registros and st.button("Recalcular puntualidad en sesión", use_container_width=True):
        for item in st.session_state.registros:
            h_meta, diff_m, cump = evaluate_punctuality(
                item["hora_final"], use_cutoff, cutoff_time, int(tolerance_min)
            )
            item["hora_meta"] = h_meta
            item["diferencia_min"] = diff_m if diff_m is not None else ""
            item["cumplimiento"] = cump
        st.success("Métricas de puntualidad actualizadas.")

    st.divider()
    st.caption("La selección territorial se aplica a las nuevas capturas cargadas.")
    st.caption("Los resultados permanecen en la sesión actual; exporta el CSV antes de cerrar.")

upload_tab, results_tab, panels_tab, info_tab = st.tabs(
    ["📤 Procesar capturas", "📋 Resultados y correcciones", "📊 Paneles", "ℹ️ Acerca del prototipo"]
)

with upload_tab:
    st.subheader("Importación masiva")
    files = st.file_uploader(
        "Selecciona imágenes JPG, PNG o WEBP (hasta 100 por lote)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        help="Sube capturas de pantalla recibidas por WhatsApp. No se conecta a WhatsApp automáticamente.",
    )
    if files:
        st.caption(f"{len(files)} archivo(s) seleccionados · territorio: {departamento} / {municipio} / {distrito}")

    if st.button("Analizar capturas", type="primary", disabled=not files):
        if len(files) > 100:
            st.error("El máximo es 100 archivos por lote. Divide la carga en lotes más pequeños.")
        else:
            existing_hashes = {i["hash_corto"] for i in st.session_state.registros}
            bar = st.progress(0, text="Iniciando OCR...")
            added = 0
            for idx, uploaded in enumerate(files):
                raw = uploaded.getvalue()
                hash_hex = hashlib.sha256(raw).hexdigest()
                smallhash = hash_hex[:16]
                record_id = len(st.session_state.registros) + 1
                result: dict[str, Any] = {
                    "status": "error", "time": None, "observations": "",
                    "selected": None, "candidates": [], "preview_bytes": raw,
                }
                is_duplicate = smallhash in existing_hashes
                if len(raw) > 15 * 1024 * 1024:
                    result["observations"] = "Archivo mayor a 15 MB; no procesado."
                elif is_duplicate:
                    result.update(
                        status="duplicada",
                        observations="Archivo idéntico a otro ya registrado en esta sesión.",
                    )
                else:
                    try:
                        result = analyze_image(raw)
                    except (ValueError, RuntimeError, OSError) as exc:
                        result["observations"] = str(exc)

                hora_detectada = result.get("time") or ""
                h_meta, diff_m, cump = evaluate_punctuality(
                    hora_detectada, use_cutoff, cutoff_time, int(tolerance_min)
                )

                item = {
                    "id": record_id,
                    "fecha_actividad": activity_date.isoformat(),
                    "departamento": departamento,
                    "municipio": municipio,
                    "distrito": distrito,
                    "archivo": uploaded.name,
                    "hora_ocr": hora_detectada,
                    "hora_final": hora_detectada,
                    "hora_meta": h_meta,
                    "diferencia_min": diff_m if diff_m is not None else "",
                    "cumplimiento": cump,
                    "estado": result.get("status", "error"),
                    "observaciones": result.get("observations", ""),
                    "hash_corto": smallhash,
                    "_bytes": result.get("preview_bytes", raw),
                    "_selected": result.get("selected"),
                    "_candidates": result.get("candidates", []),
                }
                st.session_state.registros.append(item)
                if not is_duplicate:
                    existing_hashes.add(smallhash)
                added += 1
                bar.progress((idx + 1) / len(files), text=f"Procesando {idx + 1} de {len(files)}...")
            st.success(f"Se incorporaron {added} registros a la sesión. Revisa los resultados en la siguiente pestaña.")

with results_tab:
    st.subheader("Resultados de la sesión (Edición rápida)")
    if not st.session_state.registros:
        st.warning("Todavía no hay capturas procesadas. Ve a 'Procesar capturas' para subir archivos.")
    else:
        st.caption("💡 Puedes editar directamente la columna **Hora Final** o **Observaciones** en la tabla y presionar 'Aplicar cambios de la tabla'.")
        frame = pd.DataFrame(table_rows(), columns=COLUMNS)
        edit_cols = [
            "id", "archivo", "departamento", "municipio", "distrito",
            "hora_ocr", "hora_final", "diferencia_min", "cumplimiento", "estado", "observaciones",
        ]
        edited_df = st.data_editor(
            frame[edit_cols],
            use_container_width=True,
            hide_index=True,
            disabled=["id", "archivo", "hora_ocr", "diferencia_min", "cumplimiento"],
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "hora_ocr": st.column_config.TextColumn("Hora OCR", width="small"),
                "hora_final": st.column_config.TextColumn("Hora Final (HH:MM)", width="small"),
                "diferencia_min": st.column_config.TextColumn("Dif. (min)", width="small"),
                "cumplimiento": st.column_config.TextColumn("Cumplimiento", width="medium"),
                "estado": st.column_config.SelectboxColumn(
                    "Estado",
                    options=["detectada", "corregida", "revision", "sin_hora", "duplicada", "error"],
                    width="medium",
                ),
            },
            key="main_data_editor",
        )

        col_btn1, col_btn2, _ = st.columns([1, 1, 2])
        with col_btn1:
            if st.button("💾 Aplicar cambios de la tabla", type="primary", use_container_width=True):
                errors = []
                items_by_id = {i["id"]: i for i in st.session_state.registros}
                for _, row in edited_df.iterrows():
                    rec = items_by_id[int(row["id"])]
                    new_h = str(row["hora_final"] or "").strip()
                    if new_h and not is_valid_hhmm(new_h):
                        errors.append(f"Registro #{rec['id']}: formato inválido '{new_h}' (usa HH:MM).")
                        continue
                    if new_h != rec["hora_final"]:
                        rec["hora_final"] = new_h
                        rec["estado"] = "corregida"
                    else:
                        rec["estado"] = str(row["estado"])
                    rec["departamento"] = str(row["departamento"])
                    rec["municipio"] = str(row["municipio"])
                    rec["distrito"] = str(row["distrito"])
                    rec["observaciones"] = str(row["observaciones"])
                    h_meta, diff_m, cump = evaluate_punctuality(
                        rec["hora_final"], use_cutoff, cutoff_time, int(tolerance_min)
                    )
                    rec["hora_meta"] = h_meta
                    rec["diferencia_min"] = diff_m if diff_m is not None else ""
                    rec["cumplimiento"] = cump
                if errors:
                    for err in errors:
                        st.error(err)
                else:
                    st.success("Cambios guardados y puntualidad recalculada.")
                    st.rerun()

        with col_btn2:
            st.download_button(
                "📥 Descargar resultados CSV",
                export_csv(),
                file_name=f"capturas_{activity_date.isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.divider()
        st.markdown("### 🔍 Inspección visual y corrección detallada")
        solo_revision = st.checkbox(
            "Mostrar solo registros pendientes de revisión / sin hora / error",
            value=False,
        )
        items_filtrados = [
            i for i in st.session_state.registros
            if not solo_revision or i["estado"] in ("revision", "sin_hora", "error")
        ]

        if not items_filtrados:
            st.success("🎉 ¡Excelente! No hay registros pendientes de revisión con este filtro.")
        else:
            options = {
                f"#{i['id']} · {i['archivo']} · [{status_tag(i['estado'])}] · Hora: {i['hora_final'] or '---'}": i["id"]
                for i in items_filtrados
            }
            picked = st.selectbox("Selecciona una captura para inspeccionar", list(options.keys()))
            current = next(i for i in st.session_state.registros if i["id"] == options[picked])

            left, right = st.columns([1, 1], gap="large")
            with left:
                try:
                    st.image(
                        annotated_preview(current["_bytes"], current["_selected"]),
                        caption="Recuadro verde: hora sugerida por OCR",
                        use_container_width=True,
                    )
                except (OSError, ValueError):
                    st.warning("No se puede mostrar la vista previa.")
            with right:
                st.write(f"**Hora sugerida por OCR:** `{current['hora_ocr'] or 'No identificada'}`")
                st.write(f"**Estado actual:** {status_tag(current['estado'])} | **Cumplimiento:** `{current['cumplimiento']}`")
                st.write(f"**Observaciones:** {current['observaciones']}")
                candidates = current.get("_candidates", [])
                if candidates:
                    st.write("**Todas las horas detectadas en la imagen (de arriba hacia abajo):**")
                    st.code(", ".join(f"{c['source_text']} -> {c['time']}" for c in candidates))

                with st.form(key=f"corregir_{current['id']}"):
                    new_time = st.text_input("Hora final corregida (HH:MM, 24 h)", value=current["hora_final"])
                    dep_list = ["Sin clasificar"] + DEPARTAMENTOS
                    dep_idx = dep_list.index(current["departamento"]) if current["departamento"] in dep_list else 0
                    new_dep = st.selectbox("Departamento del registro", dep_list, index=dep_idx)
                    new_mun = st.text_input("Municipio del registro", value=current["municipio"])
                    new_dis = st.text_input("Distrito del registro", value=current["distrito"])
                    new_obs = st.text_input("Observaciones", value=current["observaciones"])
                    submitted = st.form_submit_button("Guardar corrección individual")

                if submitted:
                    if new_time.strip() and not is_valid_hhmm(new_time.strip()):
                        st.error("Usa formato HH:MM de 24 horas, por ejemplo 06:00 o 18:30.")
                    else:
                        current["hora_final"] = new_time.strip()
                        current["departamento"] = new_dep
                        current["municipio"] = new_mun.strip() or "Sin clasificar"
                        current["distrito"] = new_dis.strip() or "Sin clasificar"
                        current["observaciones"] = new_obs.strip()
                        current["estado"] = "corregida"
                        h_meta, diff_m, cump = evaluate_punctuality(
                            current["hora_final"], use_cutoff, cutoff_time, int(tolerance_min)
                        )
                        current["hora_meta"] = h_meta
                        current["diferencia_min"] = diff_m if diff_m is not None else ""
                        current["cumplimiento"] = cump
                        st.success("Corrección guardada.")
                        st.rerun()

        st.divider()
        if st.button("🗑️ Vaciar sesión", type="secondary"):
            st.session_state.registros = []
            st.rerun()

with panels_tab:
    st.subheader("Paneles de rendimiento y cumplimiento")
    if not st.session_state.registros:
        st.info("Procesa algunas capturas para visualizar estadísticas reales de la sesión.")
    else:
        level = st.radio("Nivel de consulta", ["Nacional", "Departamental", "Municipal", "Distrital"], horizontal=True)
        sub = pd.DataFrame(table_rows(), columns=COLUMNS)

        if level in ("Departamental", "Municipal", "Distrital"):
            departments = sorted(sub["departamento"].dropna().unique().tolist())
            selected_dep = st.selectbox("Departamento a consultar", departments, key="panel_dep")
            sub = sub[sub["departamento"] == selected_dep]
        if level in ("Municipal", "Distrital"):
            municipios = sorted(sub["municipio"].dropna().unique().tolist())
            selected_mun = st.selectbox("Municipio a consultar", municipios, key="panel_mun")
            sub = sub[sub["municipio"] == selected_mun]
        if level == "Distrital":
            district_names = sorted(sub["distrito"].dropna().unique().tolist())
            selected_dis = st.selectbox("Distrito a consultar", district_names, key="panel_dis")
            sub = sub[sub["distrito"] == selected_dis]

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Total capturas", len(sub))
        k2.metric("En tiempo ✅", int((sub["cumplimiento"] == "En tiempo").sum()))
        k3.metric("Tardías ⏳", int((sub["cumplimiento"] == "Tardía").sum()))
        k4.metric("Para revisión ⚠️", int(sub["estado"].isin(["revision", "sin_hora", "error"]).sum()))
        k5.metric("Duplicadas 🔁", int((sub["estado"] == "duplicada").sum()))

        st.markdown("#### Resumen agrupado por territorio")
        group_col = "departamento" if level == "Nacional" else ("municipio" if level == "Departamental" else "distrito")
        resumen = (
            sub.groupby(group_col, dropna=False)
            .agg(
                Total=("id", "count"),
                En_Tiempo=("cumplimiento", lambda s: (s == "En tiempo").sum()),
                Tardias=("cumplimiento", lambda s: (s == "Tardía").sum()),
                Pendientes_Revision=("estado", lambda s: s.isin(["revision", "sin_hora", "error"]).sum()),
            )
            .reset_index()
        )
        resumen["% Efectividad a tiempo"] = (
            (resumen["En_Tiempo"] / resumen["Total"].replace(0, 1)) * 100
        ).round(1).astype(str) + "%"
        st.dataframe(resumen, use_container_width=True, hide_index=True)

        st.markdown("#### Detalle de registros filtrados")
        st.dataframe(
            sub[["id", "fecha_actividad", "departamento", "municipio", "distrito", "archivo", "hora_final", "diferencia_min", "cumplimiento", "estado"]],
            use_container_width=True,
            hide_index=True,
        )

with info_tab:
    st.markdown("""
    ### Alcance de la versión MVP 0.4
    - **Catálogo Territorial Completo:** 14 departamentos, 44 municipios y 262 distritos de El Salvador estandarizados.
    - **Preprocesamiento Inteligente:** Soporte mejorado para capturas en Modo Oscuro y compresión en memoria para evitar caídas del servidor.
    - **Métrica de Puntualidad:** Cálculo automático de minutos de retraso y clasificación (*En tiempo*, *Tardía*, *Antes de inicio*).
    - **Edición en Tabla:** Corrección masiva directamente en la tabla de resultados sin ir registro por registro.
    """)