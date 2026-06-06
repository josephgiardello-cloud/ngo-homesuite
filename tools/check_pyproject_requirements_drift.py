#!/usr/bin/env python
"""
CI guard to detect drift between pyproject.toml and requirements.txt.

Ensures lock file stays in sync with source of truth (pyproject.toml).
Runs as CI step before tests to catch version/dependency divergence early.

Usage:
  python tools/check_pyproject_requirements_drift.py
  
Exit code 0 = no drift, 1 = drift detected (guidance provided).
"""

import sys
from pathlib import Path
import re


def extract_dependencies(file_path: Path) -> dict:
    """Extract version specs from requirements-like file."""
    deps = {}
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse "package==1.2.3" or "package>=1.2.3"
            match = re.match(r"^([a-zA-Z0-9\-_.]+)\s*([=!<>~]+.+)?$", line)
            if match:
                pkg_name = match.group(1).lower()
                version_spec = match.group(2) or ""
                deps[pkg_name] = version_spec
    return deps


def extract_pyproject_deps(file_path: Path) -> dict:
    """Extract dependencies from pyproject.toml [project] dependencies."""
    deps = {}
    with open(file_path) as f:
        content = f.read()
    
    # Simple regex to find dependencies section
    dep_match = re.search(
        r'dependencies\s*=\s*\[(.*?)\]',
        content,
        re.DOTALL
    )
    if dep_match:
        deps_text = dep_match.group(1)
        for line in deps_text.split(','):
            line = line.strip().strip('"').strip("'")
            if line:
                match = re.match(r"^([a-zA-Z0-9\-_.]+)\s*(.+)?$", line)
                if match:
                    pkg_name = match.group(1).lower()
                    version_spec = match.group(2) or ""
                    deps[pkg_name] = version_spec
    return deps


def normalize_dep_name(name: str) -> str:
    """Normalize package names (Flask-SQLAlchemy -> flask-sqlalchemy)."""
    return name.lower().replace("_", "-")


def check_drift(pyproject_path: Path, requirements_path: Path) -> bool:
    """
    Check for drift between pyproject.toml and requirements.txt.
    
    Returns True if drift detected, False otherwise.
    """
    pyproject_deps = extract_pyproject_deps(pyproject_path)
    requirements_deps = extract_dependencies(requirements_path)
    
    # Normalize keys
    pyproject_deps = {normalize_dep_name(k): v for k, v in pyproject_deps.items()}
    requirements_deps = {normalize_dep_name(k): v for k, v in requirements_deps.items()}
    
    drift_found = False
    
    # Check for packages in pyproject but missing in requirements
    missing_in_req = set(pyproject_deps.keys()) - set(requirements_deps.keys())
    if missing_in_req:
        print(f"âŒ DRIFT: Packages in pyproject.toml but NOT in requirements.txt:")
        for pkg in sorted(missing_in_req):
            print(f"   - {pkg}{pyproject_deps[pkg]}")
        drift_found = True
    
    # Check for packages in requirements but missing in pyproject
    extra_in_req = set(requirements_deps.keys()) - set(pyproject_deps.keys())
    if extra_in_req:
        print(f"âŒ DRIFT: Packages in requirements.txt but NOT in pyproject.toml:")
        for pkg in sorted(extra_in_req):
            print(f"   - {pkg}{requirements_deps[pkg]}")
        drift_found = True
    
    if not drift_found:
        print("âœ… No drift detected between pyproject.toml and requirements.txt")
    else:
        print("\nGuidance to fix:")
        print("  1. Update pyproject.toml [project] dependencies")
        print("  2. Run: pip-compile -o requirements.txt pyproject.toml")
        print("  3. Commit both files together")
    
    return drift_found


def main():
    """Check drift between pyproject.toml and requirements.txt."""
    repo_root = Path(__file__).parent.parent
    pyproject = repo_root / "pyproject.toml"
    requirements = repo_root / "requirements.txt"
    
    if not pyproject.exists():
        print(f"âŒ Error: {pyproject} not found")
        return 1
    
    if not requirements.exists():
        print(f"âŒ Error: {requirements} not found")
        return 1
    
    drift_found = check_drift(pyproject, requirements)
    return 1 if drift_found else 0


if __name__ == "__main__":
    sys.exit(main())
