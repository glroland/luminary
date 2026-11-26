import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import export_openapi


def test_committed_openapi_spec_is_current():
    assert export_openapi.SPEC_PATH.exists(), "run `make openapi`"
    assert export_openapi.SPEC_PATH.read_text() == export_openapi.render(), (
        "docs/openapi.json is stale; run `make openapi` and commit it"
    )
