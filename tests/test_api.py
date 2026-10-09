from fastapi.testclient import TestClient

from precertly.api.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_policies():
    ids = {p["id"] for p in client.get("/policies").json()}
    assert "bariatric-surgery" in ids


def test_unknown_policy_is_404():
    assert client.get("/policies/nope").status_code == 404


def test_requirements_for_code():
    body = client.get("/requirements/43775").json()
    assert body[0]["policy_id"] == "bariatric-surgery"
    assert any("35" in text for text in body[0]["criteria"])


def test_requirements_for_unknown_code_is_404():
    assert client.get("/requirements/99999").status_code == 404
