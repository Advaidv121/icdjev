# Public API

## ICDLookup

```python
from icdjev import ICDLookup

with ICDLookup(jev_key='YOUR_JEV_KEY', catalogue_path='/path/to/who.xml') as icd:
    matches = icd.get_icd(note)
    review = icd.get_icd(note, include_candidates=3)
    best = icd.get_icd(note, include_candidates=3, best_score=True)
```

Initialize once and reuse. Without a context manager, call `close()` when finished. `jev_key=None` reads `TYPESAFE_API_KEY`. Initialization loads the local catalogue without hosted judgment or automatic download.

Optional constructor parameters: `model='jev-1.13.0'`, `timeout=60` (HTTP-operation seconds, not total run deadline), `threshold=0.8`, `max_depth=8`, `batch_size=32`.

## Call options

`get_icd(text, *, threshold=None, include_candidates=0, best_score=False)`

- `threshold`: acceptance cutoff in `(0, 1]`. None inherits the instance default. Overrides apply to this call only; actual Jev scores are never changed.
- `include_candidates`: nonnegative integer, default 0. Zero returns a `list[ICDCode]`. A positive N returns `ICDReviewResult` with up to N highest-scoring evaluated assignable candidates, sorted by support score descending then code. Booleans are rejected; migrate `True` to the desired count and `False` to 0. Chapters/blocks/parent codes do not consume the limit.
- `best_score`: boolean, default False. Normal mode requires both traversal and exact-code verification to meet the cutoff. True bypasses those acceptance checks for evaluated assignable codes: all are verified, ranked by the minimum of traversal and verification, and the top N returned as matches (or one match when include_candidates=0). Below-threshold matches are explicitly flagged. This mode can return very weak suggestions and can add hosted verification calls; it is intended for human review, not confirmed diagnoses.

Best-score mode does not remove the cutoff used to explore hierarchy branches. It ranks evaluated codes, not every code in the catalogue. It can still return an empty result if no assignable candidates were evaluated. Candidate count limits output, not search or hosted processing. In normal mode it does not limit accepted matches or add hosted calls.

## ICDCode

Fields: `code`, `label`, `path` (ancestor codes through this code), `probability`, `below_threshold`, `threshold_bypassed`.

`probability` is the minimum of traversal and verification scores, a model support score rather than validated clinical confidence. In normal mode the two flags are False. In best-score mode a match failing either check has both flags True. The API does not assign primary/secondary encounter roles. Human review is required in all modes.

## ICDReviewResult

```python
review = icd.get_icd(note, include_candidates=3, best_score=True)
for match in review.matches:
    print(match.code, match.probability, match.threshold_bypassed)
for candidate in review.candidates:
    print(candidate.code, candidate.support_score, candidate.status, candidate.reason)
payload = review.to_dict()
```

- `matches`: normal accepted matches, or top-scoring suggestions when best_score=True.
- `candidates`: up to the requested number of actually evaluated assignable candidates, including accepted or rejected codes; deduplicated and score-ranked.
- `threshold`: effective cutoff, retained for comparison even in best-score mode.
- `best_score`: whether ranking bypass was requested.
- `truncated`: unevaluated frontier nodes remained when the depth limit was reached. False does not mean every catalogue code was evaluated, nor describe output count limiting.
- `warnings`: catalogue limitations.
- `needs_review`: always True.
- `to_dict()`: JSON-ready representation.

Each `ICDCandidate` has `code`, `label`, `path`, `kind`, `assignable`, `traversal_score`, `verification_score`, `support_score`, `status`, and `reason`. Verification None means not performed, not a zero score. Support score is traversal unless verified, then the minimum of both. Assignable describes catalogue validity, not clinical acceptance.

Returned candidate statuses:

- `accepted`: both checks meet the cutoff.
- `below_threshold`: traversal below cutoff; verification may be absent in normal mode.
- `verification_rejected`: exact-code verification below cutoff.
- `selected_below_threshold`: returned in matches by best-score override despite failing the cutoff.

Reasons describe deterministic selection/filtering decisions, not generated clinical explanations.

## Clef and document inputs

`ICDLookup(..., backend='jev', model=None, cloudflare_account_id=None, cloudflare_api_token=None)` selects Jev by default. `model=None` pins `jev-1.13.0` for Jev, or the selected Clef backend name. Clef backends are `clef` and `clef-flash`. Credentials default to `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_AUTH_TOKEN`; `.env` loading is the caller's responsibility. Clef REST transport uses the standard library and needs no TypeSafe dependency.

`get_icd(text, images=None, ...)` accepts up to four embedded images with a Clef backend. Images accompany both traversal and verification without being retained between calls. Jev rejects image evidence. Use `PreparedImage.to_data_url()` for local files.

`prepare_documents(paths, *, target_longest_edge=2048, compress=True, pages=None, max_pages=100, office_converter=None)` performs local-only preparation with the optional `documents` dependencies. Accept a single path or multiple paths. Inputs include PDFs and supported raster images; DOC/DOCX needs LibreOffice or `office_converter(path) -> PDF path`. Page selections are one-based. Each `PreparedImage` retains source and page metadata and exposes its embedded data URL; do not serialize its base64 into review logs.

`pdf_to_base64_png(path, *, pages=None, target_longest_edge=2048, compress=True, max_pages=100)` returns a list of base64 PNG strings, one per selected page. Add the `data:image/png;base64,` prefix when constructing a raw API image input.

PNG output is lossless. `compress=True` enables encoding optimization; False skips that optimization but does not disable PNG's inherent compression. Resizing is separately controlled by `target_longest_edge`, preserving aspect ratio. Image preparation does not silently reduce requested quality to satisfy service limits: oversized output raises a document error. Inspect images for small-print legibility before hosted use.

`get_icd_from_documents(paths, *, text=..., target_longest_edge=2048, compress=True, pages=None, max_pages=100, office_converter=None, threshold=None, include_candidates=3, best_score=False, document_model='clef')` returns `DocumentLookupResult`. It automatically uses a separate Clef client when the instance is configured for Jev, without changing text behavior. On a Clef instance it reuses that instance's backend; `document_model` selects the temporary client only.

- `batches`: `DocumentBatchResult` objects containing source metadata, an `ICDReviewResult`, and per-request model/token usage.
- `request_count`: actual successful hosted judgments across traversal and verification.
- `usage`: input/output token totals; None if any request did not report a count.
- `warnings`: explicit independent-batch warning when the bundle spans groups.
- `needs_review`: always True.
- `to_dict()`: JSON-ready report without embedded images.

Groups respect four images / eight MiB combined decoded file bytes. Each image must fit four MiB and sixteen megapixels; the Clef client also enforces the thirteen-MiB JSON request limit. No probability averaging, code deduplication across groups, or whole-record contradiction reconciliation is implied. Repeated images across candidate batches add token usage. Documents are evidence, not trusted instructions.

## Errors and privacy

Empty/non-string notes and invalid options raise `ValueError` before hosted judgment. Missing/invalid files raise catalogue/file errors. Missing SDK, credentials, service failures or malformed hosted results raise `icdjev.jev.JevError` for Jev or `ClefError` for Cloudflare; local document preparation raises `DocumentError`. Failures are not converted into empty reports.

Hosted inference sends the note, attached images (if any), and catalogue context to the selected provider: TypeSafe or Cloudflare. Use synthetic text for development. Obtain organizational approval and appropriate vendor agreements before patient submissions. The library does not automatically store keys, notes, or results. Treat saved reports as sensitive.

`Catalogue.load(path)` provides local-only lookup/search/validation. `ICDLookup.to_dicts(matches)` converts a matches list to dictionaries; use `review.to_dict()` for reports.
