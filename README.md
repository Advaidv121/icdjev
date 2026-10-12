# ICDLens

**Reviewable ICD-10 suggestions from text and documents, powered by decision models.**

Formerly ICDJev. The package, Python imports, CLI and repository URL remain `icdjev` for compatibility. Text-based lookup is available today; document inputs and Clef integration are planned.

[![Tests](https://github.com/Advaidv121/icdjev/actions/workflows/tests.yml/badge.svg)](https://github.com/Advaidv121/icdjev/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

ICDLens is a Python SDK for WHO ICD-10 catalogue lookup and reviewable AI-assisted coding suggestions. Instead of asking a model to generate code strings freely, it evaluates catalogue candidates using typed decision questions and exposes model-support scores for human review. Initialize once, call `get_icd(text)`, and receive typed results. A local-only CLI is included.

## Available today

- Local catalogue: inspect codes, labels, hierarchy, inclusions, exclusions and modifier-derived codes without network access.
- Optional AI lookup: traverse the catalogue and verify exact-code support using TypeSafe/Jev.
- Review reports: inspect evaluated candidates, scores, cutoff decisions and depth truncation; export JSON-ready dictionaries.

## Decision models

| Backend | Intended use | SDK status |
| --- | --- | --- |
| [TypeSafe Jev](https://typesafe.ai/) | Text-based candidate evaluation and exact-code verification | Available |
| [Cloudflare Clef](https://developers.cloudflare.com/ai/models/@cf/cloudflare/clef/) | Text and image-based decisions | Planned |
| [Cloudflare Clef-flash](https://developers.cloudflare.com/ai/models/@cf/cloudflare/clef-flash/) | Lower-latency text and image-based decisions | Planned |

These models evaluate predefined questions rather than generate free-form answers. Their scores indicate model support, not validated clinical confidence. Jev is the currently supported SDK backend; Clef and Clef-flash integration are planned.

## Planned document support

- Accept multiple images and PDFs, with optional DOC/DOCX-to-PDF conversion.
- Provide independently usable document preparation helpers: PDF pages to base64 PNG, configurable longest-edge rendering (proposed default: 2,048 pixels), and aspect-ratio preservation.
- Enable lossless PNG optimization by default, with an opt-out; keep resizing separate from encoding compression and make lossy formats explicit.
- Prepare document inputs for Clef with file/page provenance and explicit request-limit handling. Related multi-page records need an evidence-combination policy; batching alone does not provide joint review of the whole record.

Document helpers and document-based ICD lookup are roadmap items, not currently callable APIs. The SDK remains focused on ICD coding assistance, not general-purpose document extraction.

Experimental coding assistance, not a clinical diagnosis or automated billing system. Human review is required. This is WHO ICD-10, not ICD-10-CM.

## Install

Python 3.10 or newer:

```sh
# Catalogue only — no runtime dependencies:
pip install 'icdjev @ git+https://github.com/Advaidv121/icdjev.git'
# Include the optional hosted AI SDK:
pip install 'icdjev[jev] @ git+https://github.com/Advaidv121/icdjev.git'
```

The package is not yet published to PyPI. From a local clone, use `pip install .` or `pip install '.[jev]'` instead. Pin a Git commit in production for reproducibility.

## Bring your WHO catalogue

Download an official WHO ICD-10 English ClaML file and unzip it:

```sh
curl --fail --location --output icd102019en.xml.zip \
  https://icdcdn.who.int/icd10/claml/icd102019en.xml.zip
unzip icd102019en.xml.zip
```

[WHO download index](https://icdcdn.who.int/icd10/index.html) · [ClaML information](https://icdcdn.who.int/icd10/clamlinfo.html)

Edition metadata is read from the file. WHO classification data is not bundled or automatically downloaded. Review WHO's terms before using or redistributing it; the MIT code license does not cover WHO data.

## Quickstart: AI coding suggestions

Provide your TypeSafe/Jev credential through `TYPESAFE_API_KEY` in your runtime environment or secret store. Never commit a real key.

```python
from icdjev import ICDLookup

with ICDLookup(catalogue_path='icd102019en.xml') as icd:
    matches = icd.get_icd(
        'Pneumonia, unspecified organism and type, is documented after assessment.'
    )
    for match in matches:
        print(match.code, match.label, match.probability)
```

The SDK sends the note and candidate context to TypeSafe. Use synthetic text for development. Real patient submissions require organizational approval and appropriate vendor privacy/security agreements.

Initialize once and reuse for multiple notes. If you do not use `with`, call `icd.close()` when finished. You may pass `jev_key=` explicitly with a value obtained from your secret store.

### Review candidates

```python
with ICDLookup(catalogue_path='icd102019en.xml') as icd:
    review = icd.get_icd(
        'Pneumonia is documented after assessment.', include_candidates=3
    )
    payload = review.to_dict()
    for candidate in review.candidates:
        print(candidate.code, candidate.support_score, candidate.status)
```

By default, `get_icd()` returns `list[ICDCode]`. A positive `include_candidates=N` returns `ICDReviewResult` with accepted matches and up to N highest-scoring evaluated assignable candidates. Candidate limits do not limit accepted matches in normal mode.

- `threshold=0.6` overrides the acceptance cutoff for one call; the instance default is `0.8`.
- `best_score=True` verifies and returns the top evaluated codes even below the cutoff (N matches, or one if N is zero). Weak results are flagged with `below_threshold` and `threshold_bypassed`. Hierarchy exploration stays threshold-gated: this is not an exhaustive search.
- Scores represent model support, not validated clinical confidence. No primary/secondary encounter roles are assigned.

[Full API reference](docs/api.md)

## Quickstart: local catalogue

No hosted SDK or API key needed:

```python
from icdjev import Catalogue

catalogue = Catalogue.load('icd102019en.xml')
print(catalogue.get('E11.9').full_label)
print(catalogue.validate('E11.9'))
```

```sh
icdjev --file icd102019en.xml info
icdjev --file icd102019en.xml show E11.9
icdjev --file icd102019en.xml search 'diabetes'
```

[Runnable examples](examples/) · [Architecture](docs/architecture.md)

## Scope and safety

- All suggestions require human review; review reports expose `needs_review=True`.
- Model instructions reject negated, suspected, historical and family conditions, but this is not a deterministic medical guarantee.
- Catalogue exclusions and pairing rules are available for inspection; AI lookup does not enforce exclusion adjudication, dagger/asterisk pairing, or encounter sequencing. Review those rules yourself.
- Block-level external-cause modifiers are not expanded; catalogue warnings report this limitation.
- No validated clinical accuracy benchmark is claimed. Offline tests use synthetic fixtures and fake judges to verify software behavior, not medical performance.
- The library does not automatically persist keys, notes or results. Saved reports can contain sensitive information.

## Development

```sh
git clone https://github.com/Advaidv121/icdjev.git
cd icdjev
uv sync --locked --extra jev --extra dev
uv run --no-sync pytest
uv run --no-sync ruff check src tests examples
uv run --no-sync ruff format --check src tests examples
uv build
```

CI runs offline tests, lint, formatting, builds and a core-only wheel import check on Python 3.10–3.13. Hosted inference is optional, requires your own credential and may incur API charges:

```sh
uv run --extra jev python examples/simple_lookup.py \
  --file /path/to/icd102019en.xml 'Pneumonia is documented after assessment.'
```

[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

## License

Code is licensed under [MIT](LICENSE). WHO data and third-party services have separate terms. This project is not affiliated with or endorsed by WHO or TypeSafe.
