# Contributing

Open an issue to discuss substantial changes, then submit a focused pull request.
Python 3.10+ and uv are required for development.

```sh
uv sync --locked --extra jev --extra dev
uv run --no-sync pytest
uv run --no-sync ruff check src tests examples
uv run --no-sync ruff format --check src tests examples
uv build
git diff --check
```

Add regression tests for behavior changes and update the public API documentation.
Keep the core catalogue usable without the optional hosted SDK. Preserve typed
results, explicit cutoff-bypass flags, and human-review semantics.

Tests run offline using `tests/data/mini_claml.xml`, a synthetic WHO-shaped fixture,
and fake judges. They are not clinical validation. Do not add patient notes,
credentials, official WHO XML, or generated reports to a pull request. Use only
synthetic text for optional hosted tests; these incur your own API usage.

Code contributions are provided under the repository's MIT license. WHO data and
third-party services are not covered by that license.
