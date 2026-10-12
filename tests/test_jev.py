"""Offline adapter tests using the installed SDK and an HTTP mock transport."""

import builtins
import importlib
import json
import traceback
from dataclasses import is_dataclass
from types import SimpleNamespace

import pytest

from icdjev.jev import JevClient, JevError, JudgmentBatch

QUESTIONS = {
    "present": {"type": "noul", "instructions": "Is the condition present?"},
    "code": {
        "type": "choice",
        "instructions": "Select a code.",
        "criteria": {"A00": "First candidate", "none": None},
    },
}


def payload():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "present": {"type": "noul", "noul": 0.9},
            "code": {
                "type": "choice",
                "choice": "A00",
                "confidence": 0.8,
                "probabilities": {"A00": 0.9, "none": 0.1},
            },
        },
        "usage": {"input_tokens": 123, "output_tokens": 45},
    }


@pytest.fixture
def sdk(monkeypatch):
    typesafe = pytest.importorskip("typesafe_sdk")
    import httpx2

    holder = SimpleNamespace(body=payload(), requests=[], clients=[], questions=[])
    real_client = typesafe.TypeSafeClient

    def handle(request):
        holder.requests.append(request)
        return httpx2.Response(200, json=holder.body)

    def factory(**kwargs):
        client = real_client(**kwargs, transport=httpx2.MockTransport(handle))
        real_system_one = client.system_one

        def system_one(*args, **kw):
            holder.questions.append(kw["questions"])
            return real_system_one(*args, **kw)

        client.system_one = system_one
        holder.clients.append(client)
        return client

    monkeypatch.setattr(typesafe, "TypeSafeClient", factory)
    return holder


def test_normalizes_real_sdk_response_and_closes(sdk):
    with JevClient(api_key="synthetic-test-key") as client:
        result = client.judge({"text": "synthetic example"}, QUESTIONS)
    assert is_dataclass(JudgmentBatch) and isinstance(result, JudgmentBatch)
    assert result.answers == {
        "present": {"noul": 0.9},
        "code": {"choice": "A00", "confidence": 0.8, "probabilities": {"A00": 0.9, "none": 0.1}},
    }
    assert result.model == "jev-1.13.0"
    assert result.usage == {"input_tokens": 123, "output_tokens": 45}
    assert sdk.clients[0]._http_client.is_closed
    from typesafe_sdk import Choice, Noul

    assert isinstance(sdk.questions[0]["present"], Noul)
    assert isinstance(sdk.questions[0]["code"], Choice)
    body = json.loads(sdk.requests[0].content)
    assert body["model"] == "jev-1.13.0"
    assert body["state"] == {"text": "synthetic example"}
    assert sdk.requests[0].extensions["timeout"]["read"] == 60


def test_explicit_model_timeout_and_env_key(sdk, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-env-key")
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "do-not-use")
    sdk.body["model"] = "resolved-version"
    with JevClient(model="jev-latest", timeout=7.5) as client:
        result = client.judge({}, QUESTIONS)
    assert result.model == "resolved-version"
    assert json.loads(sdk.requests[0].content)["model"] == "jev-latest"
    assert sdk.requests[0].extensions["timeout"]["read"] == 7.5


def test_noul_criteria_is_preserved(sdk):
    questions = {
        "present": {**QUESTIONS["present"], "criteria": {"true": "Explicit", "false": "Absent"}}
    }
    sdk.body["answers"].pop("code")
    with JevClient(api_key="synthetic-test-key") as client:
        client.judge({}, questions)
    assert sdk.questions[0]["present"].criteria == questions["present"]["criteria"]


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"present": {"type": "noul", "noul": 0.9}},
        {**payload()["answers"], "extra": {"type": "noul", "noul": 0.5}},
    ],
)
def test_requires_exact_answer_ids(sdk, answers):
    sdk.body["answers"] = answers
    with (
        JevClient(api_key="synthetic-test-key") as client,
        pytest.raises(JevError, match="answer IDs"),
    ):
        client.judge({}, QUESTIONS)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 1.01])
@pytest.mark.parametrize("field", ["noul", "confidence", "probability"])
def test_rejects_invalid_probabilities(sdk, field, value):
    if field == "noul":
        sdk.body["answers"]["present"]["noul"] = value
    elif field == "confidence":
        sdk.body["answers"]["code"]["confidence"] = value
    else:
        sdk.body["answers"]["code"]["probabilities"]["A00"] = value
    with JevClient(api_key="synthetic-test-key") as client, pytest.raises(JevError):
        client.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "change",
    [
        {"choice": "unknown"},
        {"confidence": None},
        {"probabilities": {}},
        {"probabilities": {"A00": 1.0}},
        {"probabilities": {"A00": 0.9, "none": 0.1, "unknown": 0.0}},
        {"probabilities": {"A00": "secret", "none": 0.1}},
    ],
)
def test_rejects_malformed_choice(sdk, change):
    sdk.body["answers"]["code"].update(change)
    with JevClient(api_key="synthetic-test-key") as client, pytest.raises(JevError):
        client.judge({}, QUESTIONS)


@pytest.mark.parametrize(
    "change",
    [
        {"model": None},
        {"model": ""},
        {"usage": None},
        {"answers": None},
        {
            "answers": {
                "present": {
                    "type": "choice",
                    "choice": "A00",
                    "confidence": 1.0,
                    "probabilities": {"A00": 1.0},
                },
                "code": payload()["answers"]["code"],
            }
        },
    ],
)
def test_rejects_malformed_response(sdk, change):
    sdk.body.update(change)
    with JevClient(api_key="synthetic-test-key") as client, pytest.raises(JevError):
        client.judge({}, QUESTIONS)


def test_unreported_usage_is_not_fabricated(sdk):
    sdk.body["usage"] = {}
    with JevClient(api_key="synthetic-test-key") as client:
        result = client.judge({}, QUESTIONS)
    assert result.usage == {"input_tokens": None, "output_tokens": None}


@pytest.mark.parametrize(
    "questions",
    [
        {},
        [],
        {"id": None},
        {"id": {"type": "score", "instructions": "Unsupported"}},
        {"id": {"type": "noul", "instructions": 123}},
        {"id": {"type": "noul", "instructions": ""}},
        {1: {"type": "noul", "instructions": "Question"}},
        {"id": {"type": "choice", "instructions": "Question", "criteria": {}}},
        {"id": {"type": "choice", "instructions": "Question", "criteria": ["A00"]}},
        {"id": {"type": "noul", "instructions": "Question", "criteria": {"bad": "secret"}}},
    ],
)
def test_rejects_malformed_questions_without_request(sdk, questions):
    with JevClient(api_key="synthetic-test-key") as client, pytest.raises(JevError):
        client.judge({}, questions)
    assert not sdk.requests


def test_requires_dict_state(sdk):
    with JevClient(api_key="synthetic-test-key") as client, pytest.raises(JevError):
        client.judge("private note", QUESTIONS)
    assert not sdk.requests


def test_errors_do_not_expose_sdk_exception_notes_or_key(sdk):
    with JevClient(api_key="synthetic-test-key") as client:

        def fail(**kwargs):
            raise RuntimeError("private note synthetic-test-key")

        sdk.clients[0].system_one = fail
        state = {"text": "private note"}
        with pytest.raises(JevError) as caught:
            client.judge(state, QUESTIONS)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private note" not in rendered and "synthetic-test-key" not in rendered
    assert sdk.clients[0]._http_client.is_closed


def test_constructor_error_is_sanitized(sdk):
    invalid_key = "secret with spaces"
    with pytest.raises(JevError) as caught:
        JevClient(api_key=invalid_key)
    assert "secret" not in "".join(traceback.format_exception(caught.value))


def test_closed_client_cannot_judge(sdk):
    with JevClient(api_key="synthetic-test-key") as client:
        pass
    with pytest.raises(JevError, match="closed"):
        client.judge({}, QUESTIONS)


def test_module_import_is_lazy_and_missing_extra_is_clear(monkeypatch):
    real_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name == "typesafe_sdk":
            raise ImportError("SDK unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    import icdjev.jev as module

    module = importlib.reload(module)
    try:
        with pytest.raises(module.JevError, match="icdjev\\[jev\\]"):
            module.JevClient(api_key="synthetic-test-key")
        from icdjev import Catalogue

        assert Catalogue is not None
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        importlib.reload(module)
