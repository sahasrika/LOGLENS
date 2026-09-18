from datetime import datetime

from loglens.models import ErrorRecord
from loglens.storage.dynamodb import DynamoDBStorage
from loglens.storage.ownership import AnalysisRecord


class Client:
    def __init__(self):
        self.items = {}
        self.calls = []

    def put_item(self, **kwargs):
        self.calls.append(("put_item", kwargs))
        item = kwargs["Item"]
        self.items[(item["pk"]["S"], item["sk"]["S"])] = item

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        item = self.items.get((kwargs["Key"]["pk"]["S"], kwargs["Key"]["sk"]["S"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        partition = kwargs["ExpressionAttributeValues"][":pk"]["S"]
        prefix = kwargs["ExpressionAttributeValues"][":sk"]["S"]
        items = [item for (pk, sk), item in sorted(self.items.items())
                 if pk == partition and sk.startswith(prefix)]
        return {"Items": items}


def error():
    return ErrorRecord("fp-1", None, "failed", 1, datetime(2026, 1, 1),
                       datetime(2026, 1, 1), "ERROR", "svc", ["failed"])


def test_owned_analysis_round_trip_and_query():
    client = Client()
    storage = DynamoDBStorage("table", "region", client=client)
    analysis = AnalysisRecord("a-1", "user-a", "created", "created", "updated")

    storage.create_analysis(analysis)

    assert storage.get_analysis_for_user("user-a", "a-1") == analysis
    assert storage.list_analyses_for_user("user-a") == [analysis]
    assert not storage.list_analyses_for_user("user-b")
    assert all(name != "scan" for name, _ in client.calls)


def test_analysis_creation_and_status_update_use_conditions():
    client = Client()
    storage = DynamoDBStorage("table", "region", client=client)
    created = AnalysisRecord("a-1", "user-a", "created", "created", "updated")
    processing = AnalysisRecord("a-1", "user-a", "processing", "created", "updated")

    storage.create_analysis(created)
    storage.update_analysis(processing, expected_status="created")

    create_call = client.calls[0][1]
    update_call = client.calls[1][1]
    assert create_call["ConditionExpression"] == "attribute_not_exists(pk)"
    assert update_call["ConditionExpression"] == "#status = :expected_status"


def test_new_global_error_write_uses_conditional_create():
    client = Client()
    storage = DynamoDBStorage("table", "region", client=client)

    storage.upsert(error())

    assert client.calls[0][1]["ConditionExpression"] == "attribute_not_exists(pk)"


def test_owned_error_relationship_is_scoped():
    client = Client()
    storage = DynamoDBStorage("table", "region", client=client)
    storage.upsert(error())
    storage.associate_error("user-a", "a-1", error())

    assert storage.get_error_for_user("user-a", "fp-1").fingerprint == "fp-1"
    assert storage.get_error_for_user("user-b", "fp-1") is None
    assert [item.fingerprint for item in storage.list_errors_for_user("user-a")] == ["fp-1"]