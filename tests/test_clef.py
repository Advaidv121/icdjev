"""Offline Clef tests: real JSON/Request handling, fake urllib network boundary."""

import base64
import builtins
import copy
import importlib
import io
import json
import struct
import traceback
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from icdjev.clef import ClefClient, ClefError
from icdjev.jev import JudgmentBatch

ACCOUNT = "0123456789abcdef" * 2
TOKEN = "synthetic-token"
MIB = 1024 * 1024
QUESTIONS = {
    "present": {"type": "noul", "instructions": "Is the condition present?"},
    "code": {
        "type": "choice",
        "instructions": "Select a code.",
        "criteria": {"A00": "First candidate", "none": None},
    },
    "severity": {
        "type": "score",
        "instructions": "Rate severity.",
        "criteria": ["Absent", "Minor", "Major"],
    },
}


def payload():
    return {
        "success": True,
        "result": {
            "model": "clef",
            "answers": {
                "present": {"type": "noul", "noul": 0.9},
                "code": {
                    "type": "choice",
                    "choice": "A00",
                    "confidence": 0.8,
                    "probabilities": {"A00": 0.9, "none": 0.1},
                },
                "severity": {
                    "type": "score",
                    "score": 1.4,
                    "confidence": 0.7,
                    "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
                    "legend": {"0": "Absent", "1": "Minor", "2": "Major"},
                },
            },
            "usage": {"input_tokens": 123, "output_tokens": 0},
        },
    }


@pytest.fixture
def transport(monkeypatch):
    holder = SimpleNamespace(body=payload(), raw=None, error=None, requests=[], reads=[], closed=0)

    class Response(io.BytesIO):
        status = 200

        def read(self, size=-1):
            holder.reads.append(size)
            return super().read(size)

        def close(self):
            holder.closed += 1
            super().close()

    def open_request(request, timeout):
        holder.requests.append((request, timeout))
        if holder.error:
            raise holder.error
        raw = holder.raw if holder.raw is not None else json.dumps(holder.body).encode()
        return Response(raw)

    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda self, request, timeout: open_request(request, timeout),
    )
    monkeypatch.delenv("CLOUDFLARE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    return holder


def client(**kwargs):
    return ClefClient(api_token=TOKEN, account_id=ACCOUNT, **kwargs)


def test_rejects_choice_below_another_option(transport):
    transport.body["result"]["answers"]["code"]["probabilities"] = {"A00": 0.1, "none": 0.9}
    with pytest.raises(ClefError, match="highest"):
        client().judge({}, QUESTIONS)


def test_normalizes_and_sends_fixed_rest_request(transport):
    original = copy.deepcopy(QUESTIONS)
    with client(timeout=7.5) as api:
        batch = api.judge({"text": "synthetic"}, QUESTIONS)
    assert isinstance(batch, JudgmentBatch)
    assert batch.answers == {
        key: {k: v for k, v in value.items() if k != "type"}
        for key, value in payload()["result"]["answers"].items()
    }
    assert batch.model == "clef"
    assert batch.usage == {"input_tokens": 123, "output_tokens": 0}
    request, timeout = transport.requests[0]
    assert timeout == 7.5
    assert request.full_url == (
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/cloudflare/clef"
    )
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {
        "model": "clef",
        "state": {"text": "synthetic"},
        "questions": QUESTIONS,
    }
    assert QUESTIONS == original
    assert transport.closed == 1
    assert transport.reads == [2 * MIB + 1]


def test_env_credentials_and_flash(transport, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT.upper())
    transport.body["result"]["model"] = "clef-flash"
    with ClefClient(model="clef-flash") as api:
        batch = api.judge("note", QUESTIONS)
    request, timeout = transport.requests[0]
    assert ACCOUNT.upper() in request.full_url
    assert request.full_url.endswith("/clef-flash")
    assert json.loads(request.data)["state"] == "note"
    assert batch.model == "clef-flash" and timeout == 60


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_token": ""},
        {"api_token": " "},
        {"api_token": 1},
        {"api_token": "secret\r\nheader"},
        {"api_token": "secret with spaces"},
        {"account_id": "a" * 31},
        {"account_id": "g" * 32},
        {"account_id": " " + ACCOUNT},
        {"account_id": ACCOUNT + "\n"},
        {"account_id": None},
        {"model": " clef "},
        {"model": "@cf/cloudflare/clef"},
        {"model": "other"},
        {"model": []},
        {"timeout": True},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": "60"},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
    ],
)
def test_bad_configuration_is_rejected_without_network(transport, kwargs):
    with pytest.raises(ClefError):
        ClefClient(**{"api_token": TOKEN, "account_id": ACCOUNT, **kwargs})
    assert not transport.requests


def test_missing_credentials(transport):
    with pytest.raises(ClefError):
        ClefClient()


def test_close_is_idempotent_and_preserves_context_exception(transport):
    api = client()
    with pytest.raises(ValueError, match="original"):
        with api:
            raise ValueError("original")
    api.close()
    with pytest.raises(ClefError, match="closed"):
        api.judge({}, QUESTIONS)
    with pytest.raises(ClefError, match="closed"):
        api.__enter__()
    assert not transport.requests


@pytest.mark.parametrize(
    "questions",
    [
        {},
        [],
        {"x": None},
        {1: QUESTIONS["present"]},
        {"": QUESTIONS["present"]},
        {"bad/id": QUESTIONS["present"]},
        {"é": QUESTIONS["present"]},
        {"x" * 101: QUESTIONS["present"]},
        {str(i): QUESTIONS["present"] for i in range(65)},
        {"x": {"type": "unknown", "instructions": "Q"}},
        {"x": {"type": "noul", "instructions": " "}},
        {"x": {"type": "noul", "instructions": 1}},
        {"x": {"type": "noul", "instructions": None}},
        {"x": {"type": "noul", "instructions": "Q", "extra": "secret"}},
        {"x": {"type": "noul", "instructions": "Q", "criteria": {"bad": "Q"}}},
        {"x": {"type": "choice", "instructions": "Q", "criteria": {"only": None}}},
        {"x": {"type": "choice", "instructions": "Q", "criteria": {"": None, "a": None}}},
        {"x": {"type": "choice", "instructions": "Q", "criteria": {1: None, "a": None}}},
        {"x": {"type": "choice", "instructions": "Q", "criteria": {"a": 1, "b": None}}},
        {
            "x": {
                "type": "choice",
                "instructions": "Q",
                "criteria": {str(i): None for i in range(256)},
            }
        },
        {"x": {"type": "score", "instructions": "Q", "criteria": ["one"]}},
        {"x": {"type": "score", "instructions": "Q", "criteria": ["x"] * 11}},
        {"x": {"type": "score", "instructions": "Q", "criteria": [None, "x"]}},
    ],
)
def test_bad_questions_never_send(transport, questions):
    with client() as api, pytest.raises(ClefError):
        api.judge({}, questions)
    assert not transport.requests


@pytest.mark.parametrize("state", [[], None, 1, True, {"x": float("nan")}, {"x": object()}])
def test_bad_state_never_sends(transport, state):
    with client() as api, pytest.raises(ClefError):
        api.judge(state, QUESTIONS)
    assert not transport.requests


def test_structured_instructions_and_criteria_are_preserved(transport):
    questions = copy.deepcopy(QUESTIONS)
    questions["present"]["instructions"] = {"question": "Present?", "data": [1, 2]}
    questions["present"]["criteria"] = {"true": ["yes"], "false": None}
    questions["code"]["instructions"] = ["Pick", {"data": "code"}]
    questions["code"]["criteria"]["A00"] = {"description": "code"}
    with client() as api:
        api.judge({}, questions)
    assert json.loads(transport.requests[0][0].data)["questions"] == questions


@pytest.mark.parametrize("field", ["noul", "confidence", "probability", "score"])
@pytest.mark.parametrize("value", [True, None, "secret", -0.01, float("nan"), float("inf")])
def test_invalid_numbers(transport, field, value):
    answers = transport.body["result"]["answers"]
    if field == "probability":
        answers["code"]["probabilities"]["A00"] = value
    elif field == "confidence":
        answers["code"][field] = value
    elif field == "score":
        answers["severity"][field] = value
    else:
        answers["present"][field] = value
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "change",
    [
        {"choice": "unknown"},
        {"choice": []},
        {"probabilities": {"A00": 1.0}},
        {"probabilities": {"A00": 0.7, "none": 0.1}},
        {"probabilities": {"A00": 1.1, "none": 0.0}},
        {"probabilities": {"A00": 0.5, "none": 0.5, "extra": 0}},
        {"type": "noul"},
        {"confidence": 1.1},
    ],
)
def test_invalid_choice(transport, change):
    transport.body["result"]["answers"]["code"].update(change)
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


def test_probability_rounding_is_tolerated(transport):
    transport.body["result"]["answers"]["code"]["probabilities"] = {"A00": 0.6667, "none": 0.3334}
    with client() as api:
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "change",
    [
        {"score": 2.1},
        {"probabilities": {"0": 1.0, "1": 0.0}},
        {"legend": {"0": "Absent"}},
        {"legend": []},
        {"legend": {"0": "wrong", "1": "Minor", "2": "Major"}},
    ],
)
def test_invalid_score(transport, change):
    transport.body["result"]["answers"]["severity"].update(change)
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "change",
    [
        {"answers": {}},
        {"answers": []},
        {"answers": {**payload()["result"]["answers"], "extra": {"type": "noul", "noul": 1}}},
        {"model": None},
        {"model": " "},
        {"usage": None},
        {"usage": {"input_tokens": True, "output_tokens": 0}},
        {"usage": {"input_tokens": -1, "output_tokens": 0}},
        {"usage": {"input_tokens": 1.5, "output_tokens": 0}},
    ],
)
def test_invalid_result(transport, change):
    transport.body["result"].update(change)
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"success": 1, "result": {}},
        {"success": False, "errors": ["private note"]},
        {"success": True, "result": None},
    ],
)
def test_invalid_envelope(transport, body):
    transport.body = body
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize("raw", [b"not json private note", b"\xff", b"x" * (2 * MIB + 1)])
def test_invalid_and_oversized_response(transport, raw):
    transport.raw = raw
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)
    assert transport.closed == 1


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("private note synthetic-token"),
        urllib.error.HTTPError(
            "https://secret.example",
            401,
            "private note synthetic-token",
            {},
            io.BytesIO(b"private note"),
        ),
        TimeoutError("private note synthetic-token"),
        RuntimeError("private note synthetic-token"),
    ],
)
def test_provider_errors_are_sanitized_without_retry(transport, error):
    transport.error = error
    state = {"text": "private note"}
    with client() as api, pytest.raises(ClefError) as caught:
        api.judge(state, QUESTIONS)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private note" not in rendered and TOKEN not in rendered
    assert "secret.example" not in rendered
    assert len(transport.requests) == 1


# Genuine tiny PNG; no optional image dependency is needed for text or these checks.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII="
)


def image(data=PNG, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def test_embedded_images_sent_as_data_urls(transport):
    images = [image(), {"content_type": "image/png", "base64": base64.b64encode(PNG).decode()}]
    with client() as api:
        api.judge({}, QUESTIONS, images=images)
    assert json.loads(transport.requests[0][0].data)["images"] == [image(), image()]


@pytest.mark.parametrize(
    "images",
    [
        "url",
        {},
        [image()] * 5,
        ["https://private.example/image.png"],
        ["data:image/svg+xml;base64,AAAA"],
        ["data:image/png;base64,%%%%"],
        [{"content_type": "image/png", "base64": 1}],
        [{"content_type": "image/png", "base64": "AAAA", "url": "secret"}],
        [image(b"not an image")],
        [image(PNG, "image/jpeg")],
        [image(b"")],
        [image(PNG + b"x" * (4 * MIB))],
        [image(PNG + b"x" * (3 * MIB))] * 3,
        [image(PNG[:16] + struct.pack(">II", 5000, 5000) + PNG[24:])],
    ],
)
def test_bad_images_never_send(transport, images):
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS, images=images)
    assert not transport.requests


@pytest.mark.parametrize(
    "encoded,mime",
    [
        (
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIx"
            "wcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAw"
            "QFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
            "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmq"
            "KjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEB"
            "AQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRob"
            "HBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
            "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+P"
            "n6/9oADAMBAAIRAxEAPwD5/ooooA//2Q==",
            "image/jpeg",
        ),
        ("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAUAmJaQAA3AA/v02aAA=", "image/webp"),
    ],
)
def test_genuine_jpeg_and_webp_supported_without_optional_decoder(transport, encoded, mime):
    with client() as api:
        api.judge({}, QUESTIONS, images=[{"content_type": mime, "base64": encoded}])
    assert json.loads(transport.requests[0][0].data)["images"] == [f"data:{mime};base64,{encoded}"]


@pytest.mark.parametrize(
    "data,mime",
    [
        (PNG[:20], "image/png"),
        (PNG[:16] + struct.pack(">II", 0, 1) + PNG[24:], "image/png"),
        (b"\xff\xd8\xff\xc0\x00\x08", "image/jpeg"),
        (b"RIFF\x04\x00\x00\x00WEBP", "image/webp"),
    ],
)
def test_truncated_and_zero_dimension_headers_rejected(transport, data, mime):
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS, images=[image(data, mime)])
    assert not transport.requests


def test_exact_image_byte_limits_accepted(transport):
    padded = PNG + b"\x00" * (4 * MIB - len(PNG))
    with client() as api:
        api.judge({}, QUESTIONS, images=[image(padded), image(padded)])
    assert len(transport.requests) == 1


def test_request_body_limit(transport):
    with client() as api, pytest.raises(ClefError):
        api.judge("x" * (13 * MIB), QUESTIONS)
    assert not transport.requests


def test_nonfinite_json_in_otherwise_valid_response(transport):
    valid = json.dumps(payload()).encode()
    transport.raw = valid[:-1] + b',"extra":NaN}'
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


def test_redirects_are_blocked_before_credentials_can_be_forwarded():
    from icdjev.clef import _NoRedirect

    handler = _NoRedirect()
    request = urllib.request.Request(
        "https://api.cloudflare.com/", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    with pytest.raises(ClefError):
        handler.redirect_request(request, None, 302, "private note", {}, "https://evil.example/")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"success":true,"success":false,"result":{}}',
        b'{"success":true,"result":{},"extra":NaN}',
    ],
)
def test_nonstandard_json_rejected(transport, raw):
    transport.raw = raw
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


@pytest.mark.parametrize("count", [2, 255])
def test_choice_limit_boundaries_are_accepted(transport, count):
    criteria = {str(i): None for i in range(count)}
    questions = {"x": {"type": "choice", "instructions": "Pick.", "criteria": criteria}}
    transport.body["result"]["answers"] = {
        "x": {
            "type": "choice",
            "choice": "0",
            "confidence": 0.5,
            "probabilities": {key: 1 / count for key in criteria},
        }
    }
    with client() as api:
        assert len(api.judge({}, questions).answers["x"]["probabilities"]) == count


@pytest.mark.parametrize("count", [2, 10])
def test_score_limit_boundaries_are_accepted(transport, count):
    criteria = [str(i) for i in range(count)]
    questions = {"x": {"type": "score", "instructions": "Rate.", "criteria": criteria}}
    transport.body["result"]["answers"] = {
        "x": {
            "type": "score",
            "score": (count - 1) / 2,
            "confidence": 0.5,
            "legend": dict(zip(criteria, criteria)),
            "probabilities": {key: 1 / count for key in criteria},
        }
    }
    with client() as api:
        assert api.judge({}, questions).answers["x"]["score"] == (count - 1) / 2


def test_question_count_and_id_length_boundaries(transport):
    questions = {str(i): QUESTIONS["present"] for i in range(63)}
    questions["x" * 100] = QUESTIONS["present"]
    transport.body["result"]["answers"] = {key: {"type": "noul", "noul": 0.5} for key in questions}
    with client() as api:
        assert len(api.judge({}, questions).answers) == 64


@pytest.mark.parametrize("kind", ["present", "code", "severity"])
def test_missing_answer_type(transport, kind):
    del transport.body["result"]["answers"][kind]["type"]
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


def test_missing_score_legend_rejected_per_official_schema(transport):
    del transport.body["result"]["answers"]["severity"]["legend"]
    with client() as api, pytest.raises(ClefError):
        api.judge({}, QUESTIONS)


def test_core_import_and_text_work_without_optional_dependencies(transport, monkeypatch):
    real_import = builtins.__import__

    def restricted(name, *args, **kwargs):
        if name.split(".")[0] in {"PIL", "fitz", "typesafe_sdk", "httpx", "httpx2"}:
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", restricted)
    module = importlib.import_module("icdjev.clef")
    importlib.reload(module)
    try:
        with module.ClefClient(api_token=TOKEN, account_id=ACCOUNT) as api:
            assert api.judge({}, QUESTIONS).answers["present"] == {"noul": 0.9}
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        importlib.reload(module)
