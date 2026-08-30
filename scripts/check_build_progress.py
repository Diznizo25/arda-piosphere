"""Check the d6734528 build progress."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
from app.services import build_tracker  # noqa: E402


def main() -> None:
    b = build_tracker.get_build_for_phone("254793026694")
    if b is None:
        print("no build record for phone")
        return
    print(f"status={b.status} progress={b.progress}% stage={b.stage!r} error={b.error!r}")


if __name__ == "__main__":
    main()
