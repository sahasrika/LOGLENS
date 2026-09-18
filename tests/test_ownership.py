from __future__ import annotations

from api.auth import AuthenticatedUser
from api.service import ApplicationService, ResourceNotFoundError
from loglens.storage.base import InMemoryStorage


class FakeS3:
    def upload(self, content, filename, user_id, analysis_id, request_id=None):
        from loglens.storage.s3 import S3ObjectReference
        return S3ObjectReference("bucket", f"raw/{user_id}/{analysis_id}/{filename}", "text/plain", len(content))


def user(subject):
    return AuthenticatedUser(subject, subject, "access", "client")


def test_users_get_isolated_analyses_and_errors():
    storage = InMemoryStorage()
    service = ApplicationService(storage)
    created = service.analyze("ERROR:svc:Failure user=1", user=user("user-a"))
    fingerprint = created["errors"][0]["fingerprint"]

    assert service.get_analysis(created["analysis_id"], user=user("user-a"))["user_id"] == "user-a"
    assert service.list_errors(user=user("user-a"))[0]["fingerprint"] == fingerprint
    for operation in (
        lambda: service.get_analysis(created["analysis_id"], user=user("user-b")),
        lambda: service.get_error(fingerprint, user=user("user-b")),
        lambda: service.diagnose(fingerprint, user=user("user-b")),
    ):
        try:
            operation()
            assert False, "cross-user access unexpectedly succeeded"
        except ResourceNotFoundError:
            pass
    assert service.list_errors(user=user("user-b")) == []


def test_user_cannot_spoof_s3_owner():
    storage = InMemoryStorage()
    service = ApplicationService(storage, s3_service=FakeS3())
    result = service.validate_upload_contract(
        "events.log", 5, b"ERROR", user=user("authoritative-user")
    )

    assert result["key"].startswith("raw/authoritative-user/")
    assert "spoof" not in result["key"]


def test_analysis_status_failure_is_persisted():
    storage = InMemoryStorage()

    class FailingPipeline:
        def __init__(self, storage):
            pass

        def process(self, text):
            raise RuntimeError("processing failed")

    service = ApplicationService(storage, pipeline_factory=FailingPipeline)
    try:
        service.analyze("ERROR failure", user=user("user-a"))
    except RuntimeError:
        pass

    analyses = service.list_analyses(user=user("user-a"))
    assert len(analyses) == 1
    assert analyses[0]["status"] == "failed"


def test_duplicate_fingerprint_remains_global_but_relationships_are_owned():
    storage = InMemoryStorage()
    service = ApplicationService(storage)
    first = service.analyze("ERROR:svc:Failure user=1", user=user("user-a"))
    second = service.analyze("ERROR:svc:Failure user=2", user=user("user-b"))

    assert first["errors"][0]["fingerprint"] == second["errors"][0]["fingerprint"]
    assert len(service.list_errors(user=user("user-a"))) == 1
    assert len(service.list_errors(user=user("user-b"))) == 1


def test_same_idempotency_key_is_reused_per_user():
    service = ApplicationService(InMemoryStorage())
    first = service.analyze("ERROR:svc:Failure", user=user("user-a"), idempotency_key="retry-1")
    retry = service.analyze("ERROR:svc:Different", user=user("user-a"), idempotency_key="retry-1")
    other_user = service.analyze("ERROR:svc:Different", user=user("user-b"), idempotency_key="retry-1")

    assert retry["analysis_id"] == first["analysis_id"]
    assert other_user["analysis_id"] != first["analysis_id"]


def test_invalid_idempotency_key_is_rejected():
    service = ApplicationService(InMemoryStorage())

    try:
        service.analyze("ERROR:svc:Failure", user=user("user-a"), idempotency_key=" ")
        assert False, "invalid idempotency key was accepted"
    except ValueError as error:
        assert "idempotency" in str(error)


def test_terminal_analysis_cannot_transition_again():
    from loglens.storage.ownership import AnalysisRecord

    storage = InMemoryStorage()
    analysis = AnalysisRecord("a-1", "user-a", "created", "now", "now")
    storage.create_analysis(analysis)
    storage.update_analysis(AnalysisRecord("a-1", "user-a", "processing", "now", "now"), "created")
    storage.update_analysis(AnalysisRecord("a-1", "user-a", "completed", "now", "now"), "processing")

    try:
        storage.update_analysis(AnalysisRecord("a-1", "user-a", "processing", "now", "now"), "completed")
        assert False, "terminal status transition was accepted"
    except ValueError as error:
        assert "transition" in str(error)