"""Tests for the ABS->Grimmory migration API routes."""


def test_preview_gated_when_unconfigured(client, mock_container):
    mock_container.mock_abs_grimmory_migration_service.is_configured.return_value = False
    resp = client.post("/api/abs-grimmory-migration/preview", json={})
    assert resp.status_code == 400


def test_preview_returns_service_payload(client, mock_container):
    svc = mock_container.mock_abs_grimmory_migration_service
    svc.is_configured.return_value = True
    svc.preview.return_value = {
        "configured": True,
        "counts": {"will_migrate": 2, "unmatched": 1},
        "books": [],
    }
    resp = client.post("/api/abs-grimmory-migration/preview", json={"carry_bookmarks": False})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["counts"]["will_migrate"] == 2
    # options parsed and forwarded
    options = svc.preview.call_args[0][0]
    assert options["carry_bookmarks"] is False
    assert options["carry_listening_sessions"] is True


def test_run_gated_when_unconfigured(client, mock_container):
    mock_container.mock_abs_grimmory_migration_service.is_configured.return_value = False
    resp = client.post("/api/abs-grimmory-migration/run", json={})
    assert resp.status_code == 400


def test_run_passes_dry_run_flag(client, mock_container):
    svc = mock_container.mock_abs_grimmory_migration_service
    svc.is_configured.return_value = True
    svc.migrate.return_value = {"configured": True, "dry_run": True, "outcome_counts": {}, "results": []}
    resp = client.post("/api/abs-grimmory-migration/run", json={"dry_run": True})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    _, kwargs = svc.migrate.call_args
    assert kwargs["dry_run"] is True


def test_run_executes_when_not_dry_run(client, mock_container):
    svc = mock_container.mock_abs_grimmory_migration_service
    svc.is_configured.return_value = True
    svc.migrate.return_value = {
        "configured": True,
        "dry_run": False,
        "outcome_counts": {"migrated": 3},
        "results": [],
    }
    resp = client.post("/api/abs-grimmory-migration/run", json={"dry_run": False})
    assert resp.status_code == 200
    assert resp.get_json()["outcome_counts"]["migrated"] == 3
    _, kwargs = svc.migrate.call_args
    assert kwargs["dry_run"] is False


def test_run_passes_selected_abs_ids(client, mock_container):
    svc = mock_container.mock_abs_grimmory_migration_service
    svc.is_configured.return_value = True
    svc.migrate.return_value = {"success": True, "outcome_counts": {}, "results": []}

    resp = client.post(
        "/api/abs-grimmory-migration/run",
        json={"dry_run": False, "selected_abs_ids": ["abs-1", "abs-2"]},
    )

    assert resp.status_code == 200
    assert svc.migrate.call_args.kwargs["selected_abs_ids"] == ["abs-1", "abs-2"]


def test_run_omits_selected_abs_ids_when_absent(client, mock_container):
    svc = mock_container.mock_abs_grimmory_migration_service
    svc.is_configured.return_value = True
    svc.migrate.return_value = {"success": True, "outcome_counts": {}, "results": []}

    resp = client.post("/api/abs-grimmory-migration/run", json={"dry_run": True})

    assert resp.status_code == 200
    assert svc.migrate.call_args.kwargs["selected_abs_ids"] is None
