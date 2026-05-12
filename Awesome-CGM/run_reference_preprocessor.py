from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    harmony_dir = repo_root / "harmony"
    sys.path.insert(0, str(harmony_dir))

    from reference_eval import main as reference_eval_main

    reference_eval_main()


if __name__ == "__main__":
    main()
