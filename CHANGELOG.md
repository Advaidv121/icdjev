# Changelog

## Unreleased

### Added
- Importable `ICDLookup` interface with optional TypeSafe/Jev inference.
- Per-call thresholds, typed matches, JSON-ready review reports and ranked evaluated candidates.
- Explicitly flagged best-score suggestions below acceptance cutoffs.
- Optional hosted SDK adapter with response validation and sanitized errors.
- Offline regression tests, safe examples, Python 3.10–3.13 CI and SDK documentation.
- MIT code license and contribution/security guidance.

### Changed
- Introduce ICDLens branding and decision-model positioning; retain the `icdjev` package, imports, CLI and repository URL for compatibility.
- Distinguish available Jev lookup from planned Clef backends and document preparation in the README.

### Fixed
- Preserve fourth/fifth-character suffix hierarchy in code ranges and wildcard references, including leading zeros and upper-bound descendants.

## 0.1.0 — local baseline

### Added
- Modifier-aware WHO ICD-10 ClaML catalogue loaded from a user-supplied file.
- Edition metadata, hierarchy navigation, local search and code validation.
- Local-only CLI and synthetic fixture tests.
