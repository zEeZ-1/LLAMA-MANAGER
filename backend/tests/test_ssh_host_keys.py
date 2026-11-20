import base64
import hashlib
import subprocess
from unittest.mock import AsyncMock

import pytest

from backend.app.remote.ssh_transport import SshTransport


@pytest.fixture
def host_key(tmp_path):
    path = tmp_path / "fixture-host-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True
    )
    algorithm, encoded, *_ = path.with_suffix(".pub").read_text().split()
    fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(base64.b64decode(encoded)).digest()
    ).decode().rstrip("=")
    return {"algorithm": algorithm, "key": encoded, "fingerprint": fingerprint}


@pytest.mark.parametrize(
    "port,lookup", [(22, "192.168.50.201"), (2222, "[192.168.50.201]:2222")]
)
def test_trusted_key_matches_openssh_host_lookup(client, host_key, port, lookup):
    remote = client.app.state.remote
    remote.ssh.scan = AsyncMock(return_value=[host_key])
    node = client.post(
        "/api/v1/nodes",
        json={
            "name": "QA",
            "address": "192.168.50.201",
            "username": "qa",
            "port": port,
        },
    ).json()
    prefix = "/api/v1/nodes/" + node["id"]
    challenge = client.post(prefix + "/host-key/scan").json()
    response = client.post(
        prefix + "/host-key/trust",
        json={
            "challenge_id": challenge["challenge_id"],
            "fingerprint": host_key["fingerprint"],
        },
    )
    assert response.status_code == 200, response.text
    result = subprocess.run(
        ["ssh-keygen", "-F", lookup, "-f", str(remote.ssh.known_hosts(node))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert host_key["key"] in result.stdout


def test_legacy_default_port_repair_preserves_trust_and_refuses_other_keys(
    cfg, host_key
):
    transport = SshTransport(cfg)
    node = {
        "id": "qa",
        "address": "192.168.50.201",
        "ssh": {"port": 22},
        "identity": {"ssh_host_key_fingerprint": host_key["fingerprint"]},
    }
    path = transport.known_hosts(node)
    original = f"[192.168.50.201]:22 {host_key['algorithm']} {host_key['key']}\n"
    path.write_text(original)
    assert transport.repair_legacy_host(node)
    result = subprocess.run(
        ["ssh-keygen", "-F", node["address"], "-f", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and host_key["key"] in result.stdout
    assert not transport.repair_legacy_host(node)
    path.write_text(original)
    node["identity"]["ssh_host_key_fingerprint"] = "SHA256:different"
    assert not transport.repair_legacy_host(node)
    assert path.read_text() == original


def test_unknown_host_record_is_not_reported_as_a_changed_key():
    unknown = SshTransport.ssh_error(
        "No ED25519 host key is known for 192.168.50.12 and you have requested strict checking.\nHost key verification failed."
    )
    changed = SshTransport.ssh_error(
        "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed."
    )
    assert unknown.code == "host_key_untrusted"
    assert changed.code == "host_key_changed"
