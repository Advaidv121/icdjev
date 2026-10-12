"""Dependency-free, synchronous Cloudflare Clef REST adapter.

Credentials default to CLOUDFLARE_AUTH_TOKEN and CLOUDFLARE_ACCOUNT_ID. Only
Cloudflare's fixed HTTPS endpoint is used; redirects and retries are disabled.
Timeout is per HTTP operation, not a whole-request deadline. Errors deliberately
omit request/response bodies, credential values, and underlying exception text.

Images must be embedded PNG/JPEG/WebP data URLs or content_type/base64 objects.
This boundary checks base64, byte limits, format headers and dimensions without
optional packages. It is not a complete image decoder: use the document/image
preparation layer for full decoding and sanitization of untrusted uploads.

Schema: https://developers.cloudflare.com/workers-ai/models/clef/
"""

import base64
import binascii
import json
import os
import re
import struct
import urllib.error
import urllib.request
from math import fsum, isfinite
from numbers import Real

from .jev import JudgmentBatch

_MIB = 1024 * 1024
_MAX_RESPONSE = 2 * _MIB
_MAX_BODY = 13 * _MIB
_ID = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_MIMES = {"image/png", "image/jpeg", "image/webp"}


class ClefError(RuntimeError):
    """Invalid Clef input, failed HTTP operation, or malformed provider response."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib normally forwards Authorization on redirects, including off-host.
        raise ClefError("Cloudflare redirects are not permitted.")


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ClefError("Invalid numeric value in Cloudflare response.")
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ClefError("Invalid numeric value in Cloudflare response.") from None
    if not isfinite(value) or not low <= value <= high:
        raise ClefError("Invalid numeric value in Cloudflare response.")
    return value


def _json_value(value):
    """Reject lossy JSON coercion, non-finite numbers, and non-JSON objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ClefError("Inputs must contain valid finite JSON data.")


def _description(value, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, (str, dict, list)):
        raise ClefError("Descriptions must be strings or structured JSON data.")
    _json_value(value)


def _questions(questions):
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 64:
        raise ClefError("Questions must be a dictionary with 1 to 64 entries.")
    for name, question in questions.items():
        if not isinstance(name, str) or not _ID.fullmatch(name):
            raise ClefError("Invalid question ID; use 1 to 100 ASCII letters, digits, _, . or -.")
        if not isinstance(question, dict) or set(question) - {"type", "instructions", "criteria"}:
            raise ClefError("Invalid question fields.")
        instructions = question.get("instructions")
        _description(instructions)
        if isinstance(instructions, str) and not instructions.strip():
            raise ClefError("Question instructions must not be empty.")
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "noul":
            if criteria is not None:
                if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                    raise ClefError("Noul criteria may contain only true and false descriptions.")
                for value in criteria.values():
                    _description(value, nullable=True)
        elif kind == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                raise ClefError("Choice criteria require 2 to 255 options.")
            for key, value in criteria.items():
                if not isinstance(key, str) or not key:
                    raise ClefError("Choice option IDs must be nonempty strings.")
                _description(value, nullable=True)
        elif kind == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ClefError("Score criteria require 2 to 10 ordered levels.")
            for value in criteria:
                _description(value)
        else:
            raise ClefError("Question type must be noul, choice or score.")


def _image_dimensions(data, mime):
    """Read bounded image headers only; do not decompress pixels."""
    if mime == "image/png":
        if (
            len(data) < 33
            or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[8:16] != (b"\x00\x00\x00\rIHDR")
        ):
            raise ClefError("Invalid PNG header.")
        return struct.unpack(">II", data[16:24])
    if mime == "image/jpeg":
        if not data.startswith(b"\xff\xd8"):
            raise ClefError("Invalid JPEG header.")
        pos = 2
        while pos < len(data):
            if data[pos] != 255:
                break
            while pos < len(data) and data[pos] == 255:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > len(data):
                break
            length = int.from_bytes(data[pos : pos + 2], "big")
            if length < 2 or pos + length > len(data):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if length < 8:
                    break
                height, width = struct.unpack(">HH", data[pos + 3 : pos + 7])
                return width, height
            pos += length
        raise ClefError("Invalid JPEG dimensions.")
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ClefError("Invalid WebP header.")
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise ClefError("Invalid WebP length.")
    kind = data[12:16]
    length = int.from_bytes(data[16:20], "little")
    if length > len(data) - 20:
        raise ClefError("Invalid WebP chunk.")
    chunk = data[20 : 20 + length]
    if kind == b"VP8X" and len(chunk) >= 10:
        return (1 + int.from_bytes(chunk[4:7], "little"), 1 + int.from_bytes(chunk[7:10], "little"))
    if kind == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", chunk[6:10])
        return width & 0x3FFF, height & 0x3FFF
    if kind == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
        bits = int.from_bytes(chunk[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise ClefError("Invalid WebP dimensions.")


def _images(images):
    if not isinstance(images, (list, tuple)) or len(images) > 4:
        raise ClefError("Images must be a sequence of at most 4 embedded images.")
    result, total = [], 0
    for image in images:
        if isinstance(image, str):
            match = re.fullmatch(
                r"data:(image/(?:png|jpeg|webp));base64,(.*)", image, flags=re.IGNORECASE
            )
            if not match:
                raise ClefError("Images must be PNG, JPEG or WebP base64 data URLs.")
            mime, encoded = match.groups()
            mime = mime.lower()
        elif isinstance(image, dict) and set(image) == {"content_type", "base64"}:
            mime, encoded = image["content_type"], image["base64"]
        else:
            raise ClefError("Invalid embedded image representation.")
        if not isinstance(mime, str) or mime not in _MIMES or not isinstance(encoded, str):
            raise ClefError("Invalid image content type or base64 data.")
        # Preflight avoids decoding arbitrary-sized input before enforcing the byte limit.
        if len(encoded) > 4 * ((4 * _MIB + 2) // 3):
            raise ClefError("Each image must be at most 4 MiB decoded.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ClefError("Invalid image base64 data.") from None
        if not data or len(data) > 4 * _MIB:
            raise ClefError("Each image must be nonempty and at most 4 MiB decoded.")
        total += len(data)
        if total > 8 * _MIB:
            raise ClefError("Images must total at most 8 MiB decoded.")
        width, height = _image_dimensions(data, mime)
        if width <= 0 or height <= 0 or width * height > 16_000_000:
            raise ClefError("Each image must have positive dimensions and at most 16 megapixels.")
        result.append(f"data:{mime};base64,{encoded}")
    return result


def _invalid_constant(value):
    raise ClefError("Non-finite numbers in Cloudflare response.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClefError("Duplicate keys in Cloudflare response.")
        result[key] = value
    return result


def _result(envelope, questions):
    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        raise ClefError("Cloudflare returned an unsuccessful or malformed response.")
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise ClefError("Malformed Cloudflare result.")
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ClefError("Cloudflare response answer IDs do not match the questions exactly.")
    normalized = {}
    for name, question in questions.items():
        answer = answers[name]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ClefError("Cloudflare response answer type does not match the question.")
        if kind == "noul":
            normalized[name] = {"noul": _number(answer.get("noul"), 0, 1)}
            continue
        criteria = question["criteria"]
        expected = set(criteria) if kind == "choice" else {str(i) for i in range(len(criteria))}
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != expected:
            raise ClefError("Cloudflare probabilities do not match the criteria exactly.")
        probabilities = {key: _number(value, 0, 1) for key, value in probabilities.items()}
        # Permit four-decimal quantization per option, but never renormalize outputs.
        if abs(fsum(probabilities.values()) - 1) > max(0.001, len(expected) * 0.00005):
            raise ClefError("Cloudflare probabilities do not sum to one.")
        common = {
            "confidence": _number(answer.get("confidence"), 0, 1),
            "probabilities": probabilities,
        }
        if kind == "choice":
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in expected:
                raise ClefError("Cloudflare returned a choice outside the supplied criteria.")
            if probabilities[choice] + 0.0001 < max(probabilities.values()):
                raise ClefError("Cloudflare choice is not a highest-probability option.")
            normalized[name] = {"choice": choice, **common}
        else:
            score = _number(answer.get("score"), 0, len(criteria) - 1)
            legend = answer.get("legend")
            if not isinstance(legend, dict) or legend != {
                str(i): value for i, value in enumerate(criteria)
            }:
                raise ClefError("Cloudflare score legend does not match the ordered criteria.")
            normalized[name] = {"score": score, "legend": legend, **common}
    model, usage = result.get("model"), result.get("usage")
    if not isinstance(model, str) or not model.strip():
        raise ClefError("Cloudflare response is missing a valid model.")
    if not isinstance(usage, dict):
        raise ClefError("Cloudflare response is missing valid token usage.")
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ClefError("Cloudflare response is missing valid token usage.")
    _json_value(usage)
    return JudgmentBatch(answers=normalized, model=model, usage=usage)


class ClefClient:
    """Synchronous Clef client returning the shared :class:`JudgmentBatch`.

    Use as a context manager or call close(). Closing is idempotent; no persistent
    HTTP connection pool is retained. Explicit credentials override environment
    values without trimming or otherwise normalizing them.
    """

    def __init__(self, api_token=None, account_id=None, model="clef", timeout=60):
        api_token = os.environ.get("CLOUDFLARE_AUTH_TOKEN") if api_token is None else api_token
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") if account_id is None else account_id
        if not isinstance(api_token, str) or not re.fullmatch(r"[\x21-\x7e]+", api_token):
            raise ClefError("A nonempty Cloudflare API token without whitespace is required.")
        if not isinstance(account_id, str) or not re.fullmatch(r"[A-Fa-f0-9]{32}", account_id):
            raise ClefError("Cloudflare account ID must be exactly 32 hexadecimal characters.")
        if not isinstance(model, str) or model not in {"clef", "clef-flash"}:
            raise ClefError("Model must be clef or clef-flash.")
        try:
            valid_timeout = (
                not isinstance(timeout, bool)
                and isinstance(timeout, Real)
                and isfinite(timeout)
                and timeout > 0
            )
        except (OverflowError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise ClefError("Timeout must be a finite positive number of seconds.")
        self.model, self.timeout = model, timeout
        self._api_token = api_token
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/ai/run/@cf/cloudflare/{model}"
        )
        self._closed = False

    def __enter__(self):
        if self._closed:
            raise ClefError("Clef client is closed.")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def close(self) -> None:
        self._closed = True

    def judge(self, state: dict | str, questions: dict, *, images=None) -> JudgmentBatch:
        """Validate locally, perform one POST, and validate every returned answer."""
        if self._closed:
            raise ClefError("Clef client is closed.")
        try:
            if not isinstance(state, (dict, str)):
                raise ClefError("State must be a dictionary or string.")
            _json_value(state)
            _questions(questions)
            body = {"model": self.model, "state": state, "questions": questions}
            if images is not None:
                body["images"] = _images(images)
            raw = json.dumps(
                body, allow_nan=False, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(raw) > _MAX_BODY:
                raise ClefError("Cloudflare request body exceeds 13 MiB.")
            request = urllib.request.Request(
                self._url,
                data=raw,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._api_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ClefError("Cloudflare returned an unsuccessful HTTP status.")
                response_body = response.read(_MAX_RESPONSE + 1)
            if len(response_body) > _MAX_RESPONSE:
                raise ClefError("Cloudflare response exceeds the size limit.")
            envelope = json.loads(
                response_body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
            return _result(envelope, questions)
        except ClefError:
            raise
        except urllib.error.HTTPError as error:
            # Never read provider error bodies, and always release their stream.
            status = error.code
            try:
                error.close()
            except Exception:
                pass
            message = (
                f"Cloudflare HTTP request failed (status {status})."
                if (
                    isinstance(status, int)
                    and not isinstance(status, bool)
                    and 100 <= status <= 599
                )
                else "Cloudflare HTTP request failed."
            )
            raise ClefError(message) from None
        except Exception:  # noqa: BLE001 - untrusted transport/JSON must not leak private data
            raise ClefError("Clef judgment failed; check input, credentials and service.") from None
