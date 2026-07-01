from pathlib import Path


def test_longport_stays_on_python_312_compatible_major() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "longport==3.0.23" in requirements
