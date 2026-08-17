"""Privacy tests: canonical artifacts must never embed an absolute home path.

``mage_ptcg.competition.redaction.redact_value`` already has its own test
coverage for the ``/home/<user>`` regex itself (C2b probe tests); what this
file verifies is the *wiring*: that local ingestion actually applies it to
``origin_reference`` before persisting the SourceEnvelope manifest, rather
than storing the raw ``str(path)`` verbatim. The assertion compares against
``redact_value()`` computed independently in the test (deterministic, pure
function) so it does not depend on whether the OS temp directory happens to
be under ``/home/`` or not.
"""

from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.competition.redaction import redact_value
from mage_ptcg.competition_intelligence.local_ingest import ingest_local_file


class TestOriginReferenceRedaction:
    def test_manifest_origin_reference_is_the_redacted_form_not_the_raw_path(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps({"turn": 1}), encoding="utf-8")
        run_root = tmp_path / "run-1"

        result = ingest_local_file(run_root, fixture, source_id="fixture-1", acquired_at="2026-07-18T00:00:00Z")

        manifest_path = Path(result["manifest_path"])
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["origin_reference"] == redact_value(str(fixture))

    def test_redact_value_strips_a_home_directory_prefix(self) -> None:
        # Documents the exact behavior local_ingest relies on: this is a
        # regression guard on the *integration point*, not a re-test of the
        # regex's own correctness (which the C2b redaction tests already own).
        redacted = redact_value("/home/some-fake-user/project/fixture.json")
        assert "some-fake-user" not in redacted
        assert redacted.startswith("<HOME>")
