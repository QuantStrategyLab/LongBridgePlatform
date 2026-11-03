"""Build a bounded source overlay against independently verified serving bytes.

No network, cloud deployment or credential access. The image base and source
hash are protected operator inputs. Unrecognized source shapes fail closed.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import re
import shutil


def source_node(source, name):
    tree = ast.parse(source)
    for node in tree.body:
        if getattr(node, "name", None) == name:
            return ast.get_source_segment(source, node)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.get_source_segment(source, node)
    raise ValueError("source_shape_unrecognized")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("source_shape_unrecognized")
    return source.replace(old, new, 1)


def overlay(base, reviewed):
    result = replace_once(base, "import hashlib\n", "import hashlib\nimport hmac\n")
    result = replace_once(result, source_node(base, "_TERMINAL_ORDER_STATUSES"), source_node(reviewed, "_WORKING_ORDER_STATUSES"))
    result = replace_once(result, source_node(base, "_normalize_order"), source_node(reviewed, "_normalize_order"))
    result = replace_once(result, 'order["status"] not in _TERMINAL_ORDER_STATUSES', 'order["status"] in _WORKING_ORDER_STATUSES')
    result = replace_once(result, "    broker_reported_balances: tuple[Mapping[str, object], ...] = ()", "    broker_reported_balances: tuple[Mapping[str, object], ...] = ()\n    recent_orders: tuple[Mapping[str, object], ...] = ()\n    orders_query_start: str | None = None\n    orders_query_end: str | None = None")
    result = replace_once(result, "        open_orders=_canonical_records(open_orders),", "        open_orders=_canonical_records(open_orders),\n        recent_orders=_canonical_records(normalized_orders),\n        orders_query_start=(reference_now - lookback).isoformat(),\n        orders_query_end=reference_now.isoformat(),")
    snapshot = source_node(reviewed, "run_read_only_account_snapshot")
    snapshot = replace_once(snapshot, '        if observations.financing:\n            payload["financing"] = list(observations.financing)\n', '')
    result = replace_once(result, source_node(base, "run_read_only_account_snapshot"), snapshot)
    compile(result, "broker_reconciliation.py", "exec")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving-module", type=Path, required=True)
    parser.add_argument("--serving-module-sha256", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[a-z0-9.-]+/[a-zA-Z0-9_./-]+@sha256:[a-f0-9]{64}", args.base_image) is None:
        raise ValueError("immutable_image_required")
    raw = args.serving_module.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.serving_module_sha256:
        raise ValueError("serving_module_digest_mismatch")
    root = Path(__file__).resolve().parents[1]
    result = overlay(raw.decode(), (root / "application/broker_reconciliation.py").read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "application").mkdir()
    (args.output / "application/broker_reconciliation.py").write_text(result)
    shutil.copytree(root / "scripts/paper_observation", args.output / "scripts/paper_observation", ignore=shutil.ignore_patterns("__pycache__"))
    (args.output / "Dockerfile").write_text(f"FROM {args.base_image}\nCOPY --chmod=0644 application/broker_reconciliation.py /app/application/broker_reconciliation.py\nCOPY --chmod=0755 scripts/paper_observation /app/scripts/paper_observation\n")


if __name__ == "__main__":
    main()
