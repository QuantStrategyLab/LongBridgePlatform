"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT, _ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
if __name__ == "__main__":
    from longbridge_platform.notifications.unified import emit_archived
    import os
    import json
    try:
        print(json.dumps(emit_archived(os.environ), sort_keys=True))
    except Exception:
        print(json.dumps({"status": "blocked", "uploaded": False, "reason": "candidate_projection_failed"}))
        raise SystemExit(1)
