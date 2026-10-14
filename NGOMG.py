"""Compatibility wrapper.

The CLI has been refactored into the `ngo_homesuite` package.

- Preferred: `python -m ngo_homesuite.main`
- Legacy:    `python NGOMG.py` (delegates to the package entry point)
"""

from ngo_homesuite.main import main


if __name__ == "__main__":
    main()
