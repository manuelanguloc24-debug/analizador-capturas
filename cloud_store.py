"""Acceso centralizado a Supabase. Solo para backend de confianza.

Nunca usar la service role key en JavaScript/browser/repositorios públicos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from supabase import create_client

BUCKET = "capturas-ocr"
TABLE = "capturas"


class CloudStore:
    def __init__(self, url: str, service_role_key: str):
        if not url or not service_role_key:
            raise ValueError("Falta configurar SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")
        self.client = create_client(url, service_role_key)

    def list_records(
        self,
        department: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = self.client.table(TABLE).select("*")
        if department and department != "Todos":
            query = query.eq("departamento", department)
        if start_date:
            query = query.gte("fecha_actividad", start_date)
        if end_date:
            query = query.lte("fecha_actividad", end_date)
        return (
            query.order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )

    def has_original(self, activity_date: str, sha256: str) -> bool:
        records = (
            self.client.table(TABLE)
            .select("id")
            .eq("fecha_actividad", activity_date)
            .eq("hash_sha256", sha256)
            .neq("estado", "duplicada")
            .limit(1)
            .execute()
            .data
            or []
        )
        return bool(records)

    def save_record(
        self,
        payload: dict[str, Any],
        original_bytes: bytes | None = None,
        mime: str = "image/jpeg",
    ) -> dict[str, Any]:
        rec_id = str(uuid4())
        row = dict(payload, id=rec_id)
        path = None
        if original_bytes:
            path = f"{row['fecha_actividad']}/{rec_id}"
            self.client.storage.from_(BUCKET).upload(
                path,
                original_bytes,
                file_options={"content-type": mime, "upsert": "false"},
            )
            row["imagen_path"] = path
            row["imagen_expira_en"] = (
                datetime.now(timezone.utc) + timedelta(days=60)
            ).isoformat()
        try:
            response = self.client.table(TABLE).insert(row).execute()
            if not response.data:
                raise RuntimeError("Supabase no devolvió confirmación de guardado")
            return response.data[0]
        except Exception:
            if path:
                try:
                    self.client.storage.from_(BUCKET).remove([path])
                except Exception:
                    pass
            raise

    def update_record(self, record_id: str, payload: dict[str, Any]) -> None:
        accepted = {
            "departamento",
            "hora_final",
            "estado",
            "observaciones",
            "hora_meta",
            "diferencia_min",
            "cumplimiento",
        }
        safe_payload = {k: v for k, v in payload.items() if k in accepted}
        if not safe_payload:
            return
        self.client.table(TABLE).update(safe_payload).eq("id", record_id).execute()

    def delete_record(self, record_id: str) -> None:
        rows = (
            self.client.table(TABLE)
            .select("imagen_path")
            .eq("id", record_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            raise ValueError("El registro ya no existe")
        path = rows[0].get("imagen_path")
        if path:
            self.client.storage.from_(BUCKET).remove([path])
        self.client.table(TABLE).delete().eq("id", record_id).execute()

    def image_bytes(self, row: dict[str, Any]) -> bytes | None:
        path = row.get("imagen_path")
        if not path:
            return None
        expiry = row.get("imagen_expira_en")
        if expiry:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= expires_at:
                return None
        return self.client.storage.from_(BUCKET).download(path)

    def cleanup_expired_images(self, batch_size: int = 100) -> tuple[int, list[str]]:
        """Elimina únicamente objetos vencidos (>60 días). Los metadatos quedan almacenados."""
        cutoff = datetime.now(timezone.utc).isoformat()
        removed = 0
        errors = []
        while True:
            rows = (
                self.client.table(TABLE)
                .select("id,imagen_path")
                .not_.is_("imagen_path", "null")
                .lte("imagen_expira_en", cutoff)
                .order("imagen_expira_en")
                .limit(batch_size)
                .execute()
                .data
                or []
            )
            if not rows:
                break
            any_success = False
            for row in rows:
                try:
                    self.client.storage.from_(BUCKET).remove([row["imagen_path"]])
                    self.client.table(TABLE).update({"imagen_path": None}).eq(
                        "id", row["id"]
                    ).execute()
                    removed += 1
                    any_success = True
                except Exception as exc:
                    errors.append(f"{row['id']}: {type(exc).__name__}: {exc}")
            if not any_success:
                break
        return removed, errors