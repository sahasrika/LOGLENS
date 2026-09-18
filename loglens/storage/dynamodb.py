"""DynamoDB adapter for the LogLens storage contract."""

from __future__ import annotations

import logging
from hashlib import sha256
from datetime import datetime
from typing import Any, Optional

from loglens.models import ErrorRecord
from loglens.storage.base import BaseStorage
from loglens.storage.ownership import ANALYSIS_STATUSES, AnalysisRecord, OwnershipStorage

logger = logging.getLogger(__name__)

_RECORD_PARTITION = "ERROR_RECORDS"


class StorageDataError(ValueError):
    """Raised when a DynamoDB item cannot be converted to an ErrorRecord."""


class DynamoDBStorage(BaseStorage, OwnershipStorage):
    """Persist ErrorRecords in DynamoDB using direct-key and Query access."""

    def __init__(
        self,
        table_name: str,
        region_name: str,
        client: Any | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        if not table_name.strip():
            raise ValueError("table_name must not be empty")
        if not region_name.strip():
            raise ValueError("region_name must not be empty")
        self.table_name = table_name
        self.region_name = region_name
        self._client = client or self._create_client(region_name, endpoint_url)

    @staticmethod
    def _create_client(region_name: str, endpoint_url: str | None) -> Any:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "boto3 is required to use DynamoDBStorage"
            ) from exc
        return boto3.client("dynamodb", region_name=region_name, endpoint_url=endpoint_url)

    def upsert(self, record: ErrorRecord) -> None:
        """Insert or replace a record keyed by its fingerprint."""
        item = self._serialize_record(record)
        kwargs: dict[str, Any] = {"TableName": self.table_name, "Item": item}
        if record.occurrences == 1:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self._client.put_item(**kwargs)
        except Exception as exc:
            if record.occurrences == 1 and self._is_conditional_failure(exc):
                existing = self.get(record.fingerprint)
                if existing is not None:
                    self._merge_occurrence(existing, record)
                    self._client.put_item(
                        TableName=self.table_name,
                        Item=self._serialize_record(existing),
                    )
                    return
            logger.exception("DynamoDB upsert failed for fingerprint %s", record.fingerprint)
            raise

    @staticmethod
    def _is_conditional_failure(exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"

    @staticmethod
    def _merge_occurrence(existing: ErrorRecord, incoming: ErrorRecord) -> None:
        existing.occurrences += incoming.occurrences
        timestamps = [value for value in (existing.first_seen, incoming.first_seen) if value]
        existing.first_seen = min(timestamps) if timestamps else None
        timestamps = [value for value in (existing.last_seen, incoming.last_seen) if value]
        existing.last_seen = max(timestamps) if timestamps else None
        for sample in incoming.sample_logs:
            if len(existing.sample_logs) >= 5:
                break
            existing.sample_logs.append(sample)

    def get(self, fingerprint: str) -> Optional[ErrorRecord]:
        """Return a record by fingerprint, or None when it does not exist."""
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=self._key(fingerprint),
                ConsistentRead=True,
            )
        except Exception:
            logger.exception("DynamoDB read failed for fingerprint %s", fingerprint)
            raise

        item = response.get("Item")
        return None if item is None else self._deserialize_record(item)

    def list_all(self) -> list[ErrorRecord]:
        """List records with a partition-key Query, following pagination."""
        records: list[ErrorRecord] = []
        exclusive_start_key: dict[str, Any] | None = None
        try:
            while True:
                request: dict[str, Any] = {
                    "TableName": self.table_name,
                    "KeyConditionExpression": "#pk = :record_partition",
                    "ExpressionAttributeNames": {"#pk": "pk"},
                    "ExpressionAttributeValues": {
                        ":record_partition": {"S": _RECORD_PARTITION}
                    },
                }
                if exclusive_start_key is not None:
                    request["ExclusiveStartKey"] = exclusive_start_key
                response = self._client.query(**request)
                records.extend(
                    self._deserialize_record(item) for item in response.get("Items", [])
                )
                exclusive_start_key = response.get("LastEvaluatedKey")
                if exclusive_start_key is None:
                    break
        except Exception:
            logger.exception("DynamoDB query failed for table %s", self.table_name)
            raise
        return sorted(records, key=lambda record: record.fingerprint)

    @staticmethod
    def _key(fingerprint: str) -> dict[str, dict[str, str]]:
        return {"pk": {"S": _RECORD_PARTITION}, "sk": {"S": fingerprint}}

    @classmethod
    def _serialize_record(cls, record: ErrorRecord) -> dict[str, dict[str, Any]]:
        payload = record.to_dict()
        item: dict[str, dict[str, Any]] = {
            "pk": {"S": _RECORD_PARTITION},
            "sk": {"S": record.fingerprint},
        }
        for field_name in (
            "fingerprint",
            "error_type",
            "message",
            "occurrences",
            "first_seen",
            "last_seen",
            "severity",
            "source_service",
            "sample_logs",
        ):
            value = payload[field_name]
            if value is None:
                item[field_name] = {"NULL": True}
            elif isinstance(value, int):
                item[field_name] = {"N": str(value)}
            elif isinstance(value, list):
                item[field_name] = {"L": [{"S": entry} for entry in value]}
            else:
                item[field_name] = {"S": str(value)}
        return item

    @classmethod
    def _deserialize_record(cls, item: dict[str, dict[str, Any]]) -> ErrorRecord:
        try:
            values = {name: cls._read_attribute(item, name) for name in (
                "fingerprint", "error_type", "message", "occurrences",
                "first_seen", "last_seen", "severity", "source_service", "sample_logs",
            )}
            if not isinstance(values["fingerprint"], str):
                raise TypeError("fingerprint must be a string")
            if not isinstance(values["message"], str):
                raise TypeError("message must be a string")
            if not isinstance(values["occurrences"], int):
                raise TypeError("occurrences must be an integer")
            for name in ("first_seen", "last_seen"):
                if values[name] is not None:
                    values[name] = datetime.fromisoformat(values[name])
            if not isinstance(values["sample_logs"], list):
                raise TypeError("sample_logs must be a list")
            return ErrorRecord(**values)
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageDataError("Malformed DynamoDB ErrorRecord item") from exc

    @staticmethod
    def _read_attribute(item: dict[str, dict[str, Any]], name: str) -> Any:
        attribute = item[name]
        if "NULL" in attribute:
            return None
        if "S" in attribute:
            return attribute["S"]
        if "N" in attribute:
            return int(attribute["N"])
        if "L" in attribute:
            return [entry["S"] for entry in attribute["L"]]
        raise TypeError(f"Unsupported DynamoDB attribute for {name}")

    def create_analysis(self, analysis: AnalysisRecord) -> None:
        if analysis.status != "created":
            raise ValueError("new analysis must start in created status")
        try:
            kwargs = {
                "TableName": self.table_name,
                "Item": self._analysis_item(analysis),
            }
            if analysis.status == "created":
                kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
            self._client.put_item(**kwargs)
        except Exception:
            logger.exception("DynamoDB analysis creation failed for %s", analysis.analysis_id)
            raise

    def get_analysis_by_idempotency(self, user_id: str, idempotency_key: str) -> Optional[AnalysisRecord]:
        digest = sha256(f"{user_id}\0{idempotency_key}".encode()).hexdigest()[:32]
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=self._analysis_key(user_id, f"analysis-{digest}"),
                ConsistentRead=True,
            )
        except Exception:
            logger.exception("DynamoDB idempotency lookup failed")
            raise
        item = response.get("Item")
        return None if item is None else self._analysis_from_item(item)

    def get_analysis_for_user(self, user_id: str, analysis_id: str) -> Optional[AnalysisRecord]:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=self._analysis_key(user_id, analysis_id),
                ConsistentRead=True,
            )
        except Exception:
            logger.exception("DynamoDB owned analysis read failed")
            raise
        item = response.get("Item")
        return None if item is None else self._analysis_from_item(item)

    def list_analyses_for_user(self, user_id: str) -> list[AnalysisRecord]:
        return self._query_owned(user_id, "ANALYSIS#", self._analysis_from_item)

    def update_analysis(self, analysis: AnalysisRecord, expected_status: str | None = None) -> None:
        kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": self._analysis_item(analysis),
        }
        if expected_status is not None:
            if analysis.status not in ANALYSIS_STATUSES:
                raise ValueError("invalid analysis status")
            kwargs["ConditionExpression"] = "#status = :expected_status"
            kwargs["ExpressionAttributeNames"] = {"#status": "status"}
            kwargs["ExpressionAttributeValues"] = {":expected_status": {"S": expected_status}}
        try:
            self._client.put_item(**kwargs)
        except Exception:
            logger.exception("DynamoDB analysis update failed for %s", analysis.analysis_id)
            raise

    def associate_error(self, user_id: str, analysis_id: str, record: ErrorRecord) -> None:
        item = {
            "pk": {"S": self._user_partition(user_id)},
            "sk": {"S": self._error_relation_sort_key(record.fingerprint, analysis_id)},
            "fingerprint": {"S": record.fingerprint},
            "analysis_id": {"S": analysis_id},
        }
        try:
            self._client.put_item(TableName=self.table_name, Item=item)
        except Exception:
            logger.exception("DynamoDB error relationship creation failed")
            raise

    def get_error_for_user(self, user_id: str, fingerprint: str) -> Optional[ErrorRecord]:
        relations = self._query_owned(
            user_id,
            f"ERROR#{fingerprint}#",
            lambda item: self._read_attribute(item, "fingerprint"),
        )
        if not relations:
            return None
        return self.get(fingerprint)

    def list_errors_for_user(self, user_id: str) -> list[ErrorRecord]:
        fingerprints = self._query_owned(
            user_id,
            "ERROR#",
            lambda item: self._read_attribute(item, "fingerprint"),
        )
        records = [self.get(fingerprint) for fingerprint in sorted(set(fingerprints))]
        return [record for record in records if record is not None]

    @staticmethod
    def _user_partition(user_id: str) -> str:
        return f"USER#{user_id}"

    @classmethod
    def _analysis_key(cls, user_id: str, analysis_id: str) -> dict[str, dict[str, str]]:
        return {"pk": {"S": cls._user_partition(user_id)}, "sk": {"S": f"ANALYSIS#{analysis_id}"}}

    @staticmethod
    def _error_relation_sort_key(fingerprint: str, analysis_id: str) -> str:
        return f"ERROR#{fingerprint}#{analysis_id}"

    @classmethod
    def _analysis_item(cls, analysis: AnalysisRecord) -> dict[str, dict[str, Any]]:
        item = cls._analysis_key(analysis.user_id, analysis.analysis_id)
        values = {
            "analysis_id": analysis.analysis_id,
            "user_id": analysis.user_id,
            "status": analysis.status,
            "created_at": analysis.created_at,
            "updated_at": analysis.updated_at,
            "source_bucket": analysis.source_bucket,
            "source_key": analysis.source_key,
            "error_fingerprints": analysis.error_fingerprints or [],
            "error_message": analysis.error_message,
            "idempotency_key": analysis.idempotency_key,
        }
        for name, value in values.items():
            if value is None:
                item[name] = {"NULL": True}
            elif isinstance(value, list):
                item[name] = {"L": [{"S": entry} for entry in value]}
            else:
                item[name] = {"S": str(value)}
        return item

    @classmethod
    def _analysis_from_item(cls, item: dict[str, dict[str, Any]]) -> AnalysisRecord:
        try:
            return AnalysisRecord(
                analysis_id=cls._read_attribute(item, "analysis_id"),
                user_id=cls._read_attribute(item, "user_id"),
                status=cls._read_attribute(item, "status"),
                created_at=cls._read_attribute(item, "created_at"),
                updated_at=cls._read_attribute(item, "updated_at"),
                source_bucket=cls._read_attribute(item, "source_bucket"),
                source_key=cls._read_attribute(item, "source_key"),
                error_fingerprints=cls._read_attribute(item, "error_fingerprints"),
                error_message=cls._read_optional_attribute(item, "error_message"),
                idempotency_key=cls._read_optional_attribute(item, "idempotency_key"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageDataError("Malformed DynamoDB AnalysisRecord item") from exc

    def _query_owned(self, user_id: str, sort_prefix: str, mapper):
        items = []
        start_key = None
        while True:
            request: dict[str, Any] = {
                "TableName": self.table_name,
                "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :sk)",
                "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
                "ExpressionAttributeValues": {
                    ":pk": {"S": self._user_partition(user_id)},
                    ":sk": {"S": sort_prefix},
                },
            }
            if start_key is not None:
                request["ExclusiveStartKey"] = start_key
            try:
                response = self._client.query(**request)
            except Exception:
                logger.exception("DynamoDB owned query failed")
                raise
            items.extend(mapper(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                return items

    @classmethod
    def _read_optional_attribute(cls, item: dict[str, dict[str, Any]], name: str) -> Any:
        if name not in item:
            return None
        return cls._read_attribute(item, name)