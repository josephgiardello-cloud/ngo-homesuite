from pathlib import Path


def test_repo_root_has_no_debug_script_files() -> None:
    """Guard against committing ad-hoc debug scripts at the repository root."""
    repo_root = Path(__file__).resolve().parents[1]
    forbidden_patterns = (
        "debug_*.py",
        "*_debug.py",
        "debug-*.py",
        "tmp_debug*.py",
        "scratch*.py",
    )

    offenders: list[str] = []
    for pattern in forbidden_patterns:
        for candidate in repo_root.glob(pattern):
            if candidate.is_file():
                offenders.append(candidate.name)

    assert not offenders, (
        "Debug-style scripts found at repository root: "
        f"{', '.join(sorted(set(offenders)))}"
    )
