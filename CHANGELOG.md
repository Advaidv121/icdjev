# Changelog

## Unreleased

### Added
- Cloudflare Clef/Clef-flash text and image evaluation using the shared catalogue traversal and verification engine.
- Local PDF/image-to-base64-PNG helpers with 2,048-pixel longest-edge rendering, lossless optimization on by default, and optional DOC/DOCX conversion.
- Independent document-batch reviews with source/page provenance, actual request counts and token usage; no cross-batch score aggregation.
- Importable `ICDLookup` interface with optional TypeSafe/Jev inference.
- Per-call thresholds, typed matches, JSON-ready review reports and ranked evaluated candidates.
- Explicitly flagged best-score suggestions below acceptance cutoffs.
- Optional hosted SDK adapter with response validation and sanitized errors.
- Offline regression tests, safe examples, Python 3.10–3.13 CI and SDK documentation.
- MIT code license and contribution/security guidance.

### Changed
- Rename the GitHub repository to `icdlens` and update installation, badge and project URLs.
- Introduce ICDLens branding and decision-model positioning; retain the `icdjev` package, imports and CLI for compatibility.
- Document Jev, Clef/Clef-flash and local document preparation with runnable examples and explicit independent-batch semantics.

### Fixed
- Preserve fourth/fifth-character suffix hierarchy in code ranges and wildcard references, including leading zeros and upper-bound descendants.

## 0.1.0 — local baseline

### Added
- Modifier-aware WHO ICD-10 ClaML catalogue loaded from a user-supplied file.
- Edition metadata, hierarchy navigation, local search and code validation.
- Local-only CLI and synthetic fixture tests.
