"""Bulk-drop a folder of docs into the DocSensei inbox.

Copies every PDF/image from a folder straight into the storage inbox via
`storage.save_to_inbox`. It does NOT ingest — it just stages files. Once the
app is running, the inbox WATCHER (app/watcher.py) creates a queued `docs` row
for each new file, and the async WORKER (app/worker.py) ingests them in the
background.

Usage:
    python scripts/bulk_upload.py /path/to/folder
"""
import sys
from pathlib import Path

# allow running as a standalone script (no package install)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage
from app.config import ensure_dirs

EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/bulk_upload.py /path/to/folder")
        return 2

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"not a directory: {folder}")
        return 2

    ensure_dirs()

    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in EXTS
    )
    print(f"found {len(files)} file(s) in {folder}")

    queued = 0
    for i, f in enumerate(files, 1):
        try:
            with open(f, "rb") as fh:
                key = storage.save_to_inbox(fh, f.name)
            print(f"[{i}/{len(files)}] {f.name[:50]:50} -> inbox ({key})")
            queued += 1
        except Exception as e:
            print(f"[{i}/{len(files)}] {f.name[:50]:50} -> FAILED {e!r}")

    print(f"\ndone: {queued}/{len(files)} copied into the inbox")
    print("ingest happens in the background once the app is running "
          "(watcher queues them, worker processes them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
