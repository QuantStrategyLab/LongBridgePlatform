from pathlib import Path
import re
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONFIG_FILES = (
    REPOSITORY_ROOT / ".env.example",
    REPOSITORY_ROOT / "docs" / "hk_equity_runtime.md",
)


def test_public_gcs_examples_use_portable_bucket_placeholders():
    observed = 0

    for path in PUBLIC_CONFIG_FILES:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for uri in re.findall(r"gs://[^\s`\"']+", line):
                parsed = urlparse(uri.rstrip("\\"))
                observed += 1

                assert parsed.scheme == "gs"
                assert parsed.path not in {"", "/"}
                assert parsed.netloc == "your-bucket" or (
                    parsed.netloc.startswith("<") and parsed.netloc.endswith(">")
                ), f"{path.name}:{line_number} uses a deployment-specific GCS bucket"

    assert observed == 6


def test_snapshot_publish_prerequisites_cover_writer_and_runtime_reader():
    runtime_doc = (
        REPOSITORY_ROOT / "docs" / "hk_equity_runtime.md"
    ).read_text(encoding="utf-8")

    assert "publisher/deploy service account 对自定义目标 bucket 具备所需对象写权限" in runtime_doc
    assert "runtime service account 具备读取权限" in runtime_doc
