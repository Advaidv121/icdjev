"""Small, optional TypeSafe SDK boundary for typed Jev judgments.

The SDK is imported only when a client is constructed. Errors deliberately omit
SDK exception text and request content. Do not enable SDK body/debug logging when
sending sensitive state. Timeout is an HTTP-operation timeout in seconds (not a
whole-batch deadline, including retries).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any


class JevError(RuntimeError):
    """Invalid adapter input, unavailable SDK, or failed/malformed judgment."""


@dataclass(frozen=True)
class JudgmentBatch:
    answers: dict[str, dict]
    model: str
    usage: dict


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise JevError("Invalid probability: expected a finite number in [0, 1].")
    value = float(value)
    if not isfinite(value) or not 0 <= value <= 1:
        raise JevError("Invalid probability: expected a finite number in [0, 1].")
    return value


class JevClient:
    """Synchronous client; use as a context manager or explicitly call close().

    With api_key=None the SDK reads TYPESAFE_API_KEY. The explicit model default
    pins Jev rather than inheriting TYPESAFE_DEFAULT_MODEL. Returned model and
    usage are taken from the actual SDK response, not the requested model.
    """

    def __init__(self, api_key=None, model="jev-1.13.0", timeout=60):
        if not isinstance(model, str) or not model.strip():
            raise JevError("Model must be a nonempty string.")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, Real)
            or not isfinite(timeout)
            or timeout <= 0
        ):
            raise JevError("Timeout must be a finite positive number of seconds.")
        try:
            from typesafe_sdk import Choice, Noul, TypeSafeClient
        except ImportError:
            raise JevError("Install the optional SDK with pip install 'icdjev[jev]'.") from None
        self.model = model
        self.timeout = timeout
        self._noul = Noul
        self._choice = Choice
        self._closed = False
        try:
            self._client = TypeSafeClient(api_key=api_key, model=model, timeout=timeout)
        except Exception:  # noqa: BLE001 - SDK boundary must not leak request/secret details
            raise JevError(
                "Could not initialize TypeSafe client; check credentials and settings."
            ) from None

    def __enter__(self):
        if self._closed:
            raise JevError("Jev client is closed.")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except JevError:
            if exc_type is None:
                raise
        return False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - SDK boundary must not leak request/secret details
                raise JevError("Could not close TypeSafe client.") from None

    def _questions(self, questions: dict) -> dict:
        if not isinstance(questions, dict) or not questions:
            raise JevError("Questions must be a nonempty dictionary.")
        converted = {}
        for name, question in questions.items():
            if not isinstance(name, str) or not name or not isinstance(question, dict):
                raise JevError("Question IDs must be nonempty strings and questions dictionaries.")
            if set(question) - {"type", "instructions", "criteria"}:
                raise JevError("Question contains unsupported fields.")
            instructions = question.get("instructions")
            if not isinstance(instructions, str) or not instructions.strip():
                raise JevError("Question instructions must be a nonempty string.")
            kind = question.get("type")
            criteria = question.get("criteria")
            if kind == "noul":
                if criteria is not None and (
                    not isinstance(criteria, dict) or set(criteria) - {"true", "false"}
                ):
                    raise JevError("Noul criteria must describe true and/or false outcomes.")
                primitive = self._noul
            elif kind == "choice":
                if (
                    not isinstance(criteria, dict)
                    or not criteria
                    or any(not isinstance(key, str) or not key for key in criteria)
                ):
                    raise JevError(
                        "Choice criteria must be a nonempty dictionary of string labels."
                    )
                primitive = self._choice
            else:
                raise JevError("Question type must be noul or choice.")
            try:
                converted[name] = primitive(instructions=instructions, criteria=criteria)
            except Exception:  # noqa: BLE001 - SDK boundary must not leak request/secret details
                raise JevError(
                    "Invalid question instructions or criteria for TypeSafe SDK."
                ) from None
        return converted

    def judge(self, state: dict, questions: dict) -> JudgmentBatch:
        if self._closed:
            raise JevError("Jev client is closed.")
        if not isinstance(state, dict):
            raise JevError("State must be a dictionary.")
        converted = self._questions(questions)
        try:
            response = self._client.system_one(state=state, questions=converted)
        except Exception:  # noqa: BLE001 - SDK boundary must not leak request/secret details
            raise JevError(
                "TypeSafe judgment failed; check credentials, service, and request validity."
            ) from None
        try:
            answers = response.answers
            if not isinstance(answers, Mapping) or set(answers) != set(converted):
                raise JevError("TypeSafe response answer IDs do not match the questions exactly.")
            normalized = {}
            for name, question in converted.items():
                answer = answers[name]
                if answer.type != question.type:
                    raise JevError("TypeSafe response answer type does not match the question.")
                if question.type == "noul":
                    normalized[name] = {"noul": _probability(answer.noul)}
                else:
                    if not isinstance(answer.choice, str) or answer.choice not in question.criteria:
                        raise JevError("TypeSafe returned a choice outside the supplied criteria.")
                    probabilities = answer.probabilities
                    if not isinstance(probabilities, Mapping) or set(probabilities) != set(
                        question.criteria
                    ):
                        raise JevError(
                            "TypeSafe choice probabilities do not match the criteria exactly."
                        )
                    normalized[name] = {
                        "choice": answer.choice,
                        "confidence": _probability(answer.confidence),
                        "probabilities": {
                            key: _probability(value) for key, value in probabilities.items()
                        },
                    }
            if not isinstance(response.model, str) or not response.model.strip():
                raise JevError("TypeSafe response is missing a valid model.")
            # Usage is a real SDK Pydantic object; None means unreported, not zero.
            usage = response.usage.model_dump()
            if not isinstance(usage, dict):
                raise JevError("TypeSafe response is missing valid token usage.")
            return JudgmentBatch(answers=normalized, model=response.model, usage=usage)
        except JevError:
            raise
        except Exception:  # noqa: BLE001 - SDK boundary must not leak request/secret details
            raise JevError("Malformed TypeSafe judgment response.") from None
