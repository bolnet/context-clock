"""cachecost — the prompt-cache economics benchmark.

Measures and models where the money actually goes in a long agent session:
not the advertised input price, but the **cache write** (1.25x/2x) and
**cache read** (0.1x) rates, and the cliff when a cache entry expires.

Three layers, deliberately separated so nothing is ever estimated and
presented as measured:

* ``pricing``   — published per-token rates (Anthropic price card). Facts.
* ``usage``     — pure arithmetic over token counts. Deterministic.
* ``scenarios`` — a cache-lifecycle simulator. Derived, clearly labelled.
* ``claims``    — the claim ledger: every assertion under test, with its
  verdict and the evidence that settles it.
"""
