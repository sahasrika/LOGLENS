from datetime import datetime

import pytest

from loglens.config import AppConfig
from loglens.models import ErrorRecord
from loglens.storage.base import InMemoryStorage
from loglens.storage.dynamodb import DynamoDBStorage, StorageDataError
from loglens.storage.factory import create_storage


class FakeDynamoDBClient:
    def __init__(self):
        self.items = {}
        self.put_calls = []
        self.get_calls = []
        self.query_calls = []
        self.error = None

    def put_item(self, **kwargs):
        if self.error:
            raise self.error
        self.put_calls.append(kwargs)
        item = kwargs["Item"]
        self.items[item["sk"]["S"]] = item

    def get_item(self, **kwargs):
        if self.error:
            raise self.error
        self.get_calls.append(kwargs)
        item = self.items.get(kwargs["Key"]["sk"]["S"])
        return {} if item is None else {"Item": item}

    def query(self, **kwargs):
        if self.error:
            raise self.error
        self.query_calls.append(kwargs)
        fingerprints = sorted(self.items)
        start_key = kwargs.get("ExclusiveStartKey")
        if start_key:
            start = fingerprints.index(start_key["sk"]["S"]) + 1
            fingerprints = fingerprints[start:]
        page = fingerprints[:2]
        response = {"Items": [self.items[fp] for fp in page]}
        if len(fingerprints) > 2:
            response["LastEvaluatedKey"] = {
                "pk": {"S": "ERROR_RECORDS"},
                "sk": {"S": page[-1]},
            }
        return response


def record(fingerprint: str = "fp-1") -> ErrorRecord:
    return ErrorRecord(
        fingerprint=fingerprint,
        error_type="ValueError",
        message="Could not parse input",
        occurrences=3,
        first_seen=datetime(2026, 8, 26, 10, 0, 0),
        last_seen=datetime(2026, 8, 26, 10, 2, 0),
        severity="ERROR",
        source_service="parser",
        sample_logs=["first line", "second line"],
    )


@pytest.fixture
def client():
    return FakeDynamoDBClient()


@pytest.fixture
def storage(client):
    return DynamoDBStorage("loglens-errors", "test-region", client=client)


def test_upsert_serializes_and_get_round_trips(storage, client):
    original = record()

    storage.upsert(original)
    restored = storage.get(original.fingerprint)

    assert restored == original
    assert client.put_calls[0]["TableName"] == "loglens-errors"
    assert client.put_calls[0]["Item"]["pk"] == {"S": "ERROR_RECORDS"}
    assert client.put_calls[0]["Item"]["sk"] == {"S": "fp-1"}
    assert client.put_calls[0]["Item"]["first_seen"] == {"S": "2026-08-26T10:00:00"}


def test_duplicate_upsert_replaces_record(storage, client):
    storage.upsert(record())
    updated = record()
    updated.occurrences = 8
    storage.upsert(updated)

    assert storage.get("fp-1").occurrences == 8
    assert len(client.put_calls) == 2


def test_get_missing_returns_none(storage):
    assert storage.get("missing") is None


def test_list_all_uses_paginated_query(storage, client):
    for fingerprint in ("fp-3", "fp-1", "fp-2"):
        storage.upsert(record(fingerprint))

    records = storage.list_all()

    assert [item.fingerprint for item in records] == ["fp-1", "fp-2", "fp-3"]
    assert len(client.query_calls) == 2
    assert all("ExclusiveStartKey" not in call for call in client.query_calls[:1])
    assert client.query_calls[0]["KeyConditionExpression"] == "#pk = :record_partition"


def test_malformed_item_raises_storage_data_error(storage, client):
    client.items["bad"] = {"pk": {"S": "ERROR_RECORDS"}, "sk": {"S": "bad"}}

    with pytest.raises(StorageDataError):
        storage.get("bad")


def test_client_errors_are_propagated(storage, client):
    client.error = RuntimeError("DynamoDB unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        storage.upsert(record())
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.get("fp-1")
    with pytest.raises(RuntimeError, match="unavailable"):
        storage.list_all()


def test_local_factory_returns_in_memory_storage(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "local")

    assert isinstance(create_storage(), InMemoryStorage)


def test_aws_factory_selects_dynamodb_storage(monkeypatch):
    config = AppConfig(
        storage_mode="aws",
        aws_region="test-region",
        dynamodb_table_name="test-table",
    )
    created = {}

    class FakeStorage:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("loglens.storage.factory.DynamoDBStorage", FakeStorage)

    assert isinstance(create_storage(config), FakeStorage)
    assert created == {
        "table_name": "test-table",
        "region_name": "test-region",
        "endpoint_url": None,
    }


def test_aws_config_requires_region_and_table(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "aws")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)

    with pytest.raises(ValueError, match="AWS_REGION"):
        AppConfig.from_env()


def test_invalid_storage_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("LOGLENS_STORAGE_MODE", "remote")

    with pytest.raises(ValueError, match="local.*aws"):
        AppConfig.from_env()