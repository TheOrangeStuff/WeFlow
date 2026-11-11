"""``python -m WeFlow`` starts the app."""

from __future__ import annotations

import sys


def main() -> None:
    from WeFlow.ui.app import run

    sys.exit(run())


if __name__ == "__main__":
    main()
