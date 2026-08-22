# LogLens

A developer tool that parses application and server logs, identifies error patterns,
and produces structured data that a developer (or an AI model) can reason about.

LogLens strips runtime noise from log messages (user IDs, request IDs, IP addresses,
UUIDs), fingerprints the stable error patterns that remain, deduplicates repeated
occurrences, and returns clean structured JSON output — with zero AWS or network
dependencies for local use.

---

## Current MVP Capabilities (Phase 1 + Phase 2)

- Parse `.log` / `.txt` files or raw pasted log text
- Auto-detect and handle five common log formats
- Assemble multi-line Java and Python stack traces into single entries
- Normalize volatile tokens while preserving exception class names
- Generate deterministic SHA-256 fingerprints per unique error pattern
- Deduplicate repeated errors — same pattern with different IDs = one record
- Track `first_seen`, `last_seen`, `occurrences`, and up to 5 sample raw lines
- Bloom Filter pre-check before exact storage lookup
- Structured JSON output per unique error record
- 189 automated tests — all passing (147 core + 42 API)

---

## Phase 2 — HTTP API

### Install all dependencies

```bash
pip install mmh3==4.1.0 bitarray==2.9.2 fastapi==0.115.0 "uvicorn[standard]==0.30.6" python-multipart==0.0.9
```

For running tests also install:

```bash
pip install pytest==8.3.2 pytest-cov==5.0.0 httpx==0.27.2
```

### Start the server

```bash
uvicorn api.main:app --reload
```

The server starts on `http://127.0.0.1:8000` by default.

> **Note (Windows with multiple Python versions):** If you have more than one Python installation, invoke uvicorn explicitly through the correct interpreter:
> ```bash
> C:\Python313\python.exe -m uvicorn api.main:app --reload
> ```

### API Documentation

| URL | Description |
|-----|-------------|
| `http://127.0.0.1:8000/docs` | Swagger UI (interactive) |
| `http://127.0.0.1:8000/redoc` | ReDoc |
| `http://127.0.0.1:8000/openapi.json` | Raw OpenAPI schema |

---

### Endpoints

#### `GET /health`

Returns service status. Use this to confirm the server is running.

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "service": "loglens"
}
```

---

#### `POST /analyze` — Raw log text

Submit log text as a JSON body.

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "2026-08-16 08:00:00 ERROR [payment-service] Payment failed for user 12345"}'
```

Request body:

```json
{
  "text": "2026-08-16 08:00:00 ERROR [payment-service] Payment failed for user 12345"
}
```

---

#### `POST /analyze/upload` — File upload

Upload a `.log` or `.txt` file using `multipart/form-data`.

```bash
curl -X POST http://127.0.0.1:8000/analyze/upload \
  -F "file=@tests/fixtures/sample.log"
```

Supported file extensions: `.log`, `.txt`
Maximum file size: 10 MB

---

### Example JSON Response

```json
{
  "total_errors": 2,
  "errors": [
    {
      "fingerprint": "846a8c7218692253",
      "error_type": null,
      "message": "Payment failed for user 10001 request_id=req-aaa111",
      "occurrences": 3,
      "first_seen": "2026-08-16T08:00:15.450000",
      "last_seen": "2026-08-16T08:00:17.290000",
      "severity": "ERROR",
      "source_service": "payment-service",
      "sample_logs": [
        "2026-08-16 08:00:15,450 ERROR [payment-service] Payment failed for user 10001 request_id=req-aaa111",
        "2026-08-16 08:00:16,380 ERROR [payment-service] Payment failed for user 20002 request_id=req-bbb222"
      ]
    },
    {
      "fingerprint": "83ade548eb672569",
      "error_type": "NullPointerException",
      "message": "Unhandled exception while processing order order_id=ORD-789",
      "occurrences": 2,
      "first_seen": "2026-08-16T08:01:00",
      "last_seen": "2026-08-16T08:02:00",
      "severity": "ERROR",
      "source_service": "order-service",
      "sample_logs": [
        "2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception..."
      ]
    }
  ]
}
```

### Input validation

| Condition | HTTP status |
|-----------|------------|
| Empty or whitespace-only text | 422 |
| Missing required field | 422 |
| Unsupported file extension | 422 |
| Empty uploaded file | 422 |
| File > 10 MB | 413 |
| Malformed JSON | 422 |

---

---

## Architecture

```
Raw text input (file path or string)
        │
        ▼
  parsers/registry.py        auto-detects format
        │
        ▼
  parsers/common.py          extracts: timestamp · level · service · message
        │                             error_type · stack_trace · metadata
        ▼
  models.ParsedLog           one object per logical log entry
        │
        ▼
  normalizer.py              strips UUIDs / IDs / IPs / numbers from message
        │                    preserves exception class names (NullPointerException etc.)
        ▼
  models.NormalizedLog
        │
        ▼
  fingerprinter.py           SHA-256(level | error_type | normalized_message)[:16]
        │
        ▼
  bloom.py                   might_contain() fast pre-check
        │
        ▼
  pipeline.py                aggregates into ErrorRecord, tracks occurrences
        │
        ▼
  storage/base.py            upsert / get / list_all  (InMemoryStorage for MVP)
        │
        ▼
  list[ErrorRecord]          structured output
```

### Supported log formats

| Format | Example |
|--------|---------|
| Standard | `2026-08-16 12:00:00 ERROR [payment-service] Payment failed` |
| Logback / Log4j | `2026-08-16T12:00:00.000Z ERROR c.e.PaymentService - Payment failed` |
| Python logging | `ERROR:payment.service:Payment failed` |
| Syslog (RFC 3164) | `Aug 16 12:00:00 hostname appname[pid]: Payment failed` |
| Bare / unstructured | `ERROR Payment failed` |

---

## Project Structure

```
log-lens/
├── loglens/
│   ├── __init__.py
│   ├── models.py             ParsedLog · NormalizedLog · ErrorRecord
│   ├── normalizer.py         volatile-token substitution with exception protection
│   ├── fingerprinter.py      SHA-256 deterministic fingerprinting
│   ├── bloom.py              Bloom Filter (mmh3 + bitarray)
│   ├── pipeline.py           full processing orchestration
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py           BaseParser ABC
│   │   ├── common.py         CommonLogParser — all five formats + stack traces
│   │   └── registry.py       format detection · parser factory
│   └── storage/
│       ├── __init__.py
│       └── base.py           BaseStorage ABC · InMemoryStorage
├── tests/
│   ├── fixtures/
│   │   └── sample.log        representative realistic log file
│   ├── test_parsers.py
│   ├── test_normalizer.py
│   ├── test_fingerprinter.py
│   ├── test_bloom.py
│   └── test_pipeline.py
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Install Dependencies

Requires Python 3.11+.

**Core + API:**

```bash
pip install mmh3==4.1.0 bitarray==2.9.2 fastapi==0.115.0 "uvicorn[standard]==0.30.6" python-multipart==0.0.9
```

**Add dev/test tools:**

```bash
pip install pytest==8.3.2 pytest-cov==5.0.0 httpx==0.27.2
```

Or install everything from the project config:

```bash
pip install -e ".[dev]"
```

---

## Run Tests

```bash
python -m pytest tests/ -v
```

With coverage:

```bash
python -m pytest tests/ -v --cov=loglens --cov-report=term-missing
```

Expected result: **189 passed** (147 core + 42 API).

---

## Run a Simple Local Analysis

```python
from loglens.pipeline import LogLensPipeline

pipeline = LogLensPipeline()

# From a file
records = pipeline.process_file("path/to/your.log")

# From a string
records = pipeline.process("""
2026-08-16 12:00:00 ERROR [payment-service] Payment failed for user 12345
2026-08-16 12:00:01 ERROR [payment-service] Payment failed for user 67890
2026-08-16 12:00:02 INFO  [payment-service] Retry initiated
""")

for record in records:
    print(record.to_json())
```

---

## Quick Command to Test Against the Sample Log

```bash
python -c "
from loglens.pipeline import LogLensPipeline
import json

p = LogLensPipeline()
results = p.process_file('tests/fixtures/sample.log')
print(f'Found {len(results)} unique error pattern(s):\n')
for r in results:
    print(json.dumps(r.to_dict(), indent=2))
    print()
"
```

---

## Example Input

```
2026-08-16 08:00:15,450 ERROR [payment-service] Payment failed for user 10001 request_id=req-aaa111
2026-08-16 08:00:16,380 ERROR [payment-service] Payment failed for user 20002 request_id=req-bbb222
2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception while processing order order_id=ORD-789
	at com.example.orders.OrderProcessor.process(OrderProcessor.java:142)
Caused by: java.lang.NullPointerException: Order item list cannot be null
	at com.example.orders.OrderValidator.validate(OrderValidator.java:56)
```

## Example Structured Output

```json
[
  {
    "fingerprint": "3a9f1c72e8b04d56",
    "error_type": null,
    "message": "Payment failed for user 10001 request_id=req-aaa111",
    "occurrences": 2,
    "first_seen": "2026-08-16T08:00:15.450000",
    "last_seen": "2026-08-16T08:00:16.380000",
    "severity": "ERROR",
    "source_service": "payment-service",
    "sample_logs": [
      "2026-08-16 08:00:15,450 ERROR [payment-service] Payment failed for user 10001 request_id=req-aaa111",
      "2026-08-16 08:00:16,380 ERROR [payment-service] Payment failed for user 20002 request_id=req-bbb222"
    ]
  },
  {
    "fingerprint": "7d2e8a14b3c90f41",
    "error_type": "NullPointerException",
    "message": "Unhandled exception while processing order order_id=ORD-789",
    "occurrences": 1,
    "first_seen": "2026-08-16T08:01:00",
    "last_seen": "2026-08-16T08:01:00",
    "severity": "ERROR",
    "source_service": "order-service",
    "sample_logs": [
      "2026-08-16 08:01:00,000 ERROR [order-service] Unhandled exception..."
    ]
  }
]
```

---

## Fingerprint Algorithm

```
SHA-256( level | error_type | normalized_message )[:16]
```

- **level** — distinguishes ERROR from WARN for otherwise identical messages
- **error_type** — keeps `NullPointerException` ≠ `ClassCastException`
- **normalized_message** — stable pattern after all volatile tokens are removed

Truncating to 16 hex chars gives 64 bits of entropy.  Collision probability
across one million distinct errors is ~2.7 × 10⁻⁹.

---

## Bloom Filter

The Bloom Filter provides a fast membership pre-check before hitting storage:

| Result | Meaning |
|--------|---------|
| `might_contain() == False` | Fingerprint is **definitely new** — skip storage lookup |
| `might_contain() == True`  | Fingerprint is **possibly seen** — do exact storage lookup |

It is not the source of truth.  Default: 100,000 capacity, 1% error rate.

---

## What Is Intentionally NOT Implemented Yet

| Feature | Phase |
|---------|-------|
| HTTP API (FastAPI routes) | Phase 2 |
| AWS API Gateway / Lambda | Phase 3 |
| DynamoDB storage backend | Phase 3 |
| S3 large-file upload | Phase 3 |
| Bloom Filter persistence | Phase 3 |
| Amazon Bedrock AI diagnosis | Phase 4 |
| Frontend UI | Phase 5 |
| Authentication / Cognito | Phase 3+ |
| JSON-structured log format | Future |
| Streaming / large-file chunking | Future |

---

## Future AWS Architecture

```
Developer / Frontend
        │  HTTPS
        ▼
API Gateway  ──→  AWS Lambda  (wraps LogLensPipeline unchanged)
                      │
                      ├──→  Amazon S3          large log file uploads
                      ├──→  Amazon DynamoDB    ErrorRecord persistence
                      ├──→  Amazon Bedrock     AI diagnosis (Claude)
                      └──→  Amazon CloudWatch  operational metrics
```

The `loglens/` core engine will not change.  Only a Lambda handler and a
`DynamoDBStorage` class (implementing `BaseStorage`) need to be added.
