"""Captured bytes are stored compressed, and that must change nothing else.

The market census writes 136 KB of near-identical JSON every half hour, which
is 2.4 GB a year into a directory that is synchronised to OneDrive on this
machine. Compression is a storage decision, so the guarantees around it are
the ones worth pinning: the recorded hash still identifies what the source
sent, artifacts written before the change still read, and a stored file that
no longer decompresses is reported as tampering rather than as a gzip error.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evidence import EvidenceError, _load_artifact, _read_stored, record_artifact
from app.models import SourceArtifact

PAYLOAD = {"sorts": [{"sortId": "up-and-coming",
                      "games": [{"universeId": n, "playerCount": n * 10} for n in range(200)]}]}


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    directory = tmp_path / "artifacts"
    directory.mkdir()
    monkeypatch.setattr("app.evidence.get_settings",
                        lambda: SimpleNamespace(artifact_dir=directory))
    return directory


def store(db, payload=PAYLOAD):
    return record_artifact(db, url="https://apis.roblox.com/explore-api/v1/get-sorts",
                           retrieval_method="scheduled_market_sample",
                           content_type="application/json", payload=payload,
                           source_tier="primary", owner="roblox.com")


def test_the_recorded_hash_is_over_what_the_source_sent_not_the_stored_form(db, artifacts):
    """If the digest moved to the compressed bytes, every hash already in the
    ledger would stop matching and the change would be a silent rewrite of
    history."""
    artifact = store(db)

    canonical = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")).encode()
    on_disk = Path(artifact.raw_path).read_bytes()

    assert artifact.sha256 == hashlib.sha256(canonical).hexdigest()
    assert hashlib.sha256(on_disk).hexdigest() != artifact.sha256, "stored form is not the raw form"


def test_the_payload_survives_the_round_trip(db, artifacts):
    artifact = store(db)

    assert _load_artifact(artifact) == PAYLOAD


def test_a_census_sized_payload_is_meaningfully_smaller_on_disk(db, artifacts):
    artifact = store(db)

    on_disk = Path(artifact.raw_path).stat().st_size
    assert on_disk < artifact.raw_size / 2, (
        f"{on_disk} of {artifact.raw_size} bytes; compression is not earning its keep"
    )


def test_the_recorded_size_is_what_the_source_sent(db, artifacts):
    """The UI labels this "raw captured bytes". Recording the compressed size
    would understate every capture in the library."""
    artifact = store(db)

    canonical = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")).encode()
    assert artifact.raw_size == len(canonical)


def test_identical_payloads_still_land_on_one_file(db, artifacts):
    """The path comes from the digest of the raw bytes, so the same capture
    twice is still one file and the second write is skipped."""
    first, second = store(db), store(db)

    assert first.raw_path == second.raw_path
    assert len(list(artifacts.iterdir())) == 1


def test_the_compressed_form_is_reproducible(db, artifacts):
    """gzip writes the current time into its header by default, so the same
    bytes compressed twice would differ. Nothing depends on that today -- the
    file name comes from the raw digest -- but a store rebuilt from the same
    captures should come out byte-identical rather than merely equivalent."""
    artifact = store(db)
    again = gzip.compress(
        json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")).encode(),
        compresslevel=6, mtime=0)

    assert Path(artifact.raw_path).read_bytes() == again


def test_an_artifact_written_before_compression_still_reads(db, artifacts):
    """Existing rows keep their own uncompressed paths; there is no migration,
    so both forms have to stay readable."""
    raw = json.dumps({"data": [{"visits": 5}]}).encode()
    legacy = artifacts / (hashlib.sha256(raw).hexdigest() + ".json")
    legacy.write_bytes(raw)
    artifact = SourceArtifact(
        url="https://games.roblox.com/v1/games", publisher_owner="roblox.com",
        retrieval_method="legacy", sha256=hashlib.sha256(raw).hexdigest(),
        content_type="application/json", raw_path=str(legacy), source_tier="primary",
        raw_size=len(raw))
    db.add(artifact)
    db.flush()

    assert _load_artifact(artifact) == {"data": [{"visits": 5}]}


def test_a_stored_file_that_will_not_decompress_is_reported_as_tampering(db, artifacts):
    """Not as a gzip error. A payload that cannot be read back is a payload
    that no longer holds what was captured, which is the same failure."""
    artifact = store(db)
    Path(artifact.raw_path).write_text("tampered", encoding="utf-8")

    with pytest.raises(EvidenceError, match="hash mismatch"):
        _load_artifact(artifact)


def test_altered_but_valid_compressed_bytes_are_still_caught_by_the_hash(db, artifacts):
    """Tampering that bothers to re-compress must not slip past. The hash is
    what actually guards the content; decompression only guards readability."""
    artifact = store(db)
    Path(artifact.raw_path).write_bytes(
        gzip.compress(json.dumps({"sorts": []}).encode(), mtime=0))

    with pytest.raises(EvidenceError, match="hash mismatch"):
        _load_artifact(artifact)


def test_reading_a_plain_file_needs_no_special_casing_by_the_caller(artifacts):
    plain = artifacts / "plain.json"
    plain.write_bytes(b'{"a":1}')

    assert _read_stored(plain) == b'{"a":1}'


def test_the_library_reports_the_captured_size_not_the_file_on_disk(artifacts,
                                                                    session_factory):
    """`_artifact_size` used to stat the file. Now that the file is compressed,
    doing so would report about a sixth of the truth under a column the UI
    labels "raw captured bytes" -- and the Evidence storage figure on the home
    page would shrink by the same factor with nothing having been deleted."""
    from fastapi.testclient import TestClient

    from app import main as main_module

    with session_factory() as writer:
        artifact = store(writer)
        writer.commit()
        expected, path = artifact.raw_size, artifact.raw_path

    def override():
        with session_factory() as session:
            yield session

    main_module.app.dependency_overrides[main_module.get_db] = override
    try:
        body = TestClient(main_module.app).get("/api/sources?paged=true").json()
    finally:
        main_module.app.dependency_overrides.clear()

    reported = next(item["raw_size"] for item in body["items"])
    assert reported == expected
    assert reported > Path(path).stat().st_size, "the compressed file is what got reported"
