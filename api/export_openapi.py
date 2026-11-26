"""Write the API's OpenAPI document to docs/openapi.json (or --check it).

The spec is generated from the FastAPI app itself, so it can't drift from the
routes; the committed copy exists so API changes show up in review. Run via
`make openapi`; `make openapi-check` (and api/tests/test_openapi.py) fail if
the committed file is stale.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def render() -> str:
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    spec = render()
    if "--check" in sys.argv:
        if not SPEC_PATH.exists() or SPEC_PATH.read_text() != spec:
            print(f"{SPEC_PATH} is out of date; run `make openapi`.", file=sys.stderr)
            return 1
        return 0
    SPEC_PATH.write_text(spec)
    print(f"Wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
