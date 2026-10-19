"""Ejecuta el borrado periódico de imágenes vencidas, sin borrar los datos."""
import os
import sys

from cloud_store import CloudStore


def main() -> None:
    store = CloudStore(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    removed, errors = store.cleanup_expired_images()
    print(f"Imágenes eliminadas al cumplir 60 días: {removed}")
    for error in errors:
        print("ERROR:", error, file=sys.stderr)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()