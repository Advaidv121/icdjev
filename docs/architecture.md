# Architecture

ICDJev is a synchronous Python library, not a web service.

1. `Catalogue.load(path)` parses a user-supplied WHO ICD-10 ClaML file, builds the
   hierarchy and modifier-derived nodes, and exposes local search and validation.
2. `ICDLookup` starts with chapters plus lexical search hits, scores candidates in
   batches, and expands supported hierarchy branches up to `max_depth`.
3. Assignable candidates receive an exact-code support check. Normal results must
   meet the cutoff at both stages; their score is the minimum of the two.
4. Optional review reports expose ranked evaluated assignable candidates, decisions,
   catalogue warnings and depth truncation. Best-score mode verifies all evaluated
   assignable candidates and explicitly marks selected below-cutoff suggestions.

`jev.py` is the optional hosted boundary. The TypeSafe SDK is lazily imported when
a client is constructed; importing `icdjev` or using `Catalogue` requires no SDK.
The library does not automatically download WHO data or persist notes/results.

The catalogue exposes exclusions, notes and references. The current AI lookup
uses labels, paths and inclusions; it does not adjudicate exclusions, assign
primary/secondary roles, or enforce dagger/asterisk pairing. Human review remains
necessary. Best-score ranking is over evaluated candidates, not the whole catalogue.

See [the API reference](api.md) for the public contract.
