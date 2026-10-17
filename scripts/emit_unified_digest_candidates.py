"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notifications.digest_snapshot.digest_archive import main  # noqa: E402
if __name__ == "__main__":
    from notifications.unified import enabled, emit_archived
    import os
    import json
    if enabled():
        try:
            print(json.dumps(emit_archived(os.environ), sort_keys=True))
        except Exception:
            print(json.dumps({"status": "blocked", "uploaded": False, "reason": "candidate_projection_failed"}))
            raise SystemExit(1)
    else:
        raise SystemExit(main())
