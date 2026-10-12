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

## Errors and privacy

Empty/non-string notes and invalid options raise `ValueError` before hosted judgment. Missing/invalid files raise catalogue/file errors. Missing SDK, credentials, service failures or malformed hosted results raise `icdjev.jev.JevError`; failures are not converted into empty reports.

Hosted inference sends the note and catalogue context to TypeSafe. Use synthetic text for development. Obtain organizational approval and appropriate vendor agreements before patient submissions. The library does not automatically store keys, notes, or results. Treat saved reports as sensitive.

`Catalogue.load(path)` provides local-only lookup/search/validation. `ICDLookup.to_dicts(matches)` converts a matches list to dictionaries; use `review.to_dict()` for reports.
