import hashlib
import json
import subprocess
import sys
import tarfile

import pytest

from myllama import package_release
from myllama.package_release import package, verify


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "myllama_v99"
    (root / "bin").mkdir(parents=True)
    (root / "models").mkdir()
    binary = root / "bin/server"
    binary.write_bytes(b"fixture-runtime")
    binary.chmod(0o755)
    (root / "bin/link").symlink_to("server")
    (root / "models/model.gguf").write_bytes(b"fixture-weights")
    (root / "manifest.json").write_text('{"name":"myllama_v99"}')
    (root / "SHA256SUMS").write_text(
        hashlib.sha256(binary.read_bytes()).hexdigest()
        + "  bin/server\n"
        + "0" * 64
        + "  models/model.gguf\n"
    )
    return root


def test_runtime_roundtrip_excludes_weights_and_preserves_links(release, tmp_path):
    assert verify(release) == (1, 1)
    archive = package(release, tmp_path / "out")
    expected = archive.with_name(archive.name + ".sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    with tarfile.open(archive) as tar:
        assert all(not member.name.endswith(".gguf") for member in tar)
        assert tar.getmember("myllama_v99/bin/link").issym()
        assert tar.getmember("myllama_v99/bin/server").mode == 0o755
        tar.extractall(tmp_path / "restored", filter="data")
    assert verify(tmp_path / "restored/myllama_v99") == (1, 1)


def test_existing_artifact_cannot_be_overwritten(release, tmp_path):
    archive = package(release, tmp_path / "out")
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        package(release, tmp_path / "out")
    assert archive.read_bytes() == original


def test_outside_symlink_is_rejected_and_partial_archive_removed(release, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("not part of the release")
    (release / "escape").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlink escapes"):
        package(release, tmp_path / "rejected")
    assert not list((tmp_path / "rejected").iterdir())


def test_changed_runtime_fails_checksum_verification(release):
    (release / "bin/server").write_bytes(b"changed-runtime")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify(release)


def test_model_checksum_metadata_is_verified_and_packaged(release, tmp_path):
    metadata = release / "models/SHA256SUMS"
    metadata.write_text("0" * 64 + "  model.gguf\n")
    with (release / "SHA256SUMS").open("a") as stream:
        stream.write(
            hashlib.sha256(metadata.read_bytes()).hexdigest() + "  models/SHA256SUMS\n"
        )
    assert verify(release) == (2, 1)
    archive = package(release, tmp_path / "out")
    with tarfile.open(archive) as tar:
        assert (
            tar.extractfile("myllama_v99/models/SHA256SUMS").read()
            == metadata.read_bytes()
        )
        assert not any(member.name.endswith(".gguf") for member in tar)
    metadata.write_text("changed checksum metadata")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify(release)


@pytest.mark.parametrize("field", ["name", "release"])
def test_manifest_compatible_with_existing_and_v3_fields(release, field):
    (release / "manifest.json").write_text(json.dumps({field: release.name}))
    result = subprocess.run(
        [sys.executable, package_release.__file__, str(release), "--verify-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Verified 1 runtime files" in result.stdout
