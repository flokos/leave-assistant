"""n8n workflows as code (doc 06 §6): structural and policy checks of every export in automation/n8n/workflows.

Node types and versions are the ones n8n 2.46.6 ships (`n8n export:nodes`, M8); the live import and publish run in
the compose E2E (worklog M8).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3] / "automation" / "n8n"
WORKFLOWS = sorted((ROOT / "workflows").glob("*.json"))
NODE_VERSIONS = {  # from `n8n export:nodes` on n8nio/n8n:2.46.6
    "n8n-nodes-base.scheduleTrigger": {1, 1.1, 1.2, 1.3, 1.4},
    "n8n-nodes-base.httpRequest": {1, 2, 3, 4, 4.1, 4.2, 4.3, 4.4, 4.5},
    "n8n-nodes-base.switch": {1, 2, 3, 3.1, 3.2, 3.3, 3.4, 3.5},
    "n8n-nodes-base.executeWorkflow": {1, 1.1, 1.2, 1.3, 1.4},
    "n8n-nodes-base.executeWorkflowTrigger": {1, 1.1, 1.2, 1.3},
    "n8n-nodes-base.emailSend": {1, 2, 2.1},
    "n8n-nodes-base.if": {1, 2, 2.1, 2.2, 2.3, 2.4},
    "n8n-nodes-base.splitOut": {1, 1.1},
    "n8n-nodes-base.errorTrigger": {1},
    "n8n-nodes-base.wait": {1, 1.1},
}
INTERNAL = "http://api:8000/api/v1/internal"
SECRET_PATTERNS = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]{10,}|eyJ[a-zA-Z0-9_-]{10,}|password\"\s*:\s*\"[^\"]+\"|client_?secret\"\s*:\s*\"[^$])"
)


def load(path: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_expected_workflows_exist() -> None:
    names = {p.stem for p in WORKFLOWS}
    assert names == {
        "W0-event-poller",
        "W1-document-watchdog",
        "W2-index-check",
        "W3-retention",
        "W4-notification-email",
        "W5-calendar",
        "W6-housekeeping",
        "W7-provider-health",
        "error-handler",
    }
    ids = [load(p)["id"] for p in WORKFLOWS]
    assert len(set(ids)) == len(ids) and all(len(i) == 16 and i.isalnum() for i in ids)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_structure_and_policy(path: Path) -> None:
    data = load(path)
    all_ids = {load(p)["id"] for p in WORKFLOWS}
    error_id = load(ROOT / "workflows" / "error-handler.json")["id"]
    names = [n["name"] for n in data["nodes"]]
    assert len(set(names)) == len(names)
    for node in data["nodes"]:
        assert node["typeVersion"] in NODE_VERSIONS[node["type"]], (
            node["name"],
            node["type"],
            node["typeVersion"],
        )
        params = node["parameters"]
        if node["type"].endswith(".httpRequest"):
            url = params["url"]
            assert INTERNAL in url, (node["name"], url)  # only the internal API, on the compose network
            assert (
                params["authentication"] == "genericCredentialType"
                and params["genericAuthType"] == "oAuth2Api"
            )
            assert node["credentials"]["oAuth2Api"]["id"] == "laKeycloakAuto01"
        if node["type"].endswith(".emailSend"):
            assert node["credentials"]["smtp"]["id"] == "laMailpitSmtp001"
        if node["type"].endswith(".executeWorkflow"):
            assert params["workflowId"]["value"] in all_ids
    for source, outputs in data["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            assert all(link["node"] in names for link in branch)
    settings = data["settings"]
    assert settings["saveDataSuccessExecution"] == "none" and settings["saveDataErrorExecution"] == "all"
    assert settings["executionOrder"] == "v1" and settings["saveManualExecutions"] is False
    if path.stem != "error-handler":
        assert settings["errorWorkflow"] == error_id
    assert data["active"] is False  # published by n8n-init (n8n 2.x), never imported as active
    assert not SECRET_PATTERNS.search(path.read_text(encoding="utf-8"))


def test_every_event_is_acknowledged_by_the_poller() -> None:
    poller = load(ROOT / "workflows" / "W0-event-poller.json")
    targets = {
        link["node"] for out in poller["connections"].values() for branch in out["main"] for link in branch
    }
    acks = [n for n in poller["nodes"] if n["name"].startswith("Ack ")]
    assert len(acks) == 3 and all(a["name"] in targets for a in acks)
    switch = next(n for n in poller["nodes"] if n["type"].endswith(".switch"))
    assert switch["parameters"]["options"]["fallbackOutput"] == "extra"  # unknown types are acknowledged too


def test_credentials_template_holds_placeholders_only() -> None:
    text = (ROOT / "credentials.template.json").read_text(encoding="utf-8")
    creds = json.loads(text)
    assert {c["id"] for c in creds} == {"laKeycloakAuto01", "laMailpitSmtp001"}
    oauth = next(c for c in creds if c["type"] == "oAuth2Api")["data"]
    assert oauth["grantType"] == "clientCredentials" and oauth["clientSecret"] == "${N8N_CLIENT_SECRET}"
    assert "n8n-automation-dev" not in text
