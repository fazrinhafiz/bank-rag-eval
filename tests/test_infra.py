"""Static policy checks on infra/azure. CI also runs `terraform fmt -check` and `terraform validate`;
these tests pin the design rules that validation cannot see, and run without Terraform installed."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "azure"
MAIN = (INFRA / "main.tf").read_text()
VARIABLES = (INFRA / "variables.tf").read_text()


def blocks(text: str, header: str) -> list[str]:
    """Bodies of every `header {` block, matched by brace depth."""
    out = []
    for m in re.finditer(rf"(?m)^\s*{header}\s*{{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append(text[m.end():i - 1])
    return out


def variable_default(name: str) -> str:
    body = blocks(VARIABLES, f'variable "{name}"')[0]
    return re.search(r"(?m)^\s*default\s*=\s*(.+)$", body).group(1).strip()


def test_single_replica_because_the_audit_log_is_one_sqlite_file():
    tmpl = blocks(MAIN, "template")[0]
    assert re.search(r"min_replicas\s*=\s*1\b", tmpl) and re.search(r"max_replicas\s*=\s*1\b", tmpl)


def test_no_wal_on_the_network_share():
    assert re.search(r'name\s*=\s*"BANKRAG_AUDIT_JOURNAL_MODE"\s*\n\s*value\s*=\s*"DELETE"', MAIN)


def test_audit_volume_is_the_path_the_image_makes_writable():
    dockerfile = (ROOT / "Dockerfile").read_text()
    mount = re.search(r'audit_mount\s*=\s*"([^"]+)"', MAIN).group(1)
    assert mount == "/app/audit" and f"mkdir -p /app/traces {mount}" in dockerfile
    assert re.search(r'audit_db:\s*audit/', (ROOT / "config.yaml").read_text())


def test_container_port_matches_the_image():
    port = re.search(r"container_port\s*=\s*(\d+)", MAIN).group(1)
    assert f"EXPOSE {port}" in (ROOT / "Dockerfile").read_text()


def test_secrets_come_from_key_vault_never_literal_values():
    secrets = blocks(MAIN, "secret")
    assert len(secrets) == 2
    for body in secrets:
        assert "key_vault_secret_id" in body and "identity" in body
        assert not re.search(r"(?m)^\s*value\s*=", body)


def test_every_secret_env_var_the_app_reads_is_wired():
    for var, secret in [("BANKRAG_AUDIT_KEY", "audit-key"), ("BANKRAG_TRACE_SALT", "trace-salt")]:
        assert re.search(rf'name\s*=\s*"{var}"\s*\n\s*secret_name\s*=\s*"{secret}"', MAIN)


@pytest.mark.parametrize("pattern", [
    r"admin_enabled\s*=\s*false",                    # registry: identity pull, no admin password
    r"enable_rbac_authorization\s*=\s*true",         # key vault: RBAC, not access policies
    r"purge_protection_enabled\s*=\s*true",
    r'min_tls_version\s*=\s*"TLS1_2"',
    r"https_traffic_only_enabled\s*=\s*true",
    r"allow_nested_items_to_be_public\s*=\s*false",
    r"allow_insecure_connections\s*=\s*false",
    r'container_access_type\s*=\s*"private"',
])
def test_hardening_setting_present(pattern):
    assert re.search(pattern, MAIN), pattern


def test_private_by_default():
    assert variable_default("ingress_external") == "false"


def test_image_tag_is_required_and_mutable_tags_refused():
    body = blocks(VARIABLES, 'variable "image_tag"')[0]
    assert "default" not in body and '"latest"' in body


def test_anchor_container_is_write_once():
    body = blocks(MAIN, 'resource "azurerm_storage_container_immutability_policy" "anchors"')[0]
    assert "immutability_period_in_days" in body and "anchors.id" in body
