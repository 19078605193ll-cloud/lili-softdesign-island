# Upstream snapshot

Repository: https://github.com/19078605193ll-cloud/lili-voice-input

Commit: `b55fbfa5255028ad34937f5fbbe8b469ba26639f`

Included: Python server source/locked dependencies and TypeScript Browser SDK source/tests.
Its demo, training material and unrelated tooling are excluded.

Local server adaptations:

- Text polishing defaults `polish_enable_thinking` to `False`, so Qwen requests explicitly disable thinking when the environment switch is omitted. The `enable_thinking` parameter is sent only for Qwen models to keep standard OpenAI models compatible. DeepSeek retains its upstream `thinking.type=disabled` request.
- `server/tests/test_polishing.py` checks the serialized provider parameters and preservation of ASR text on provider failures, using the real SDK with a mocked HTTP transport.

Local SDK adaptations:

- Package exports point to TypeScript source for the host Vite build. The host bundles the AudioWorklet with `?worker&url`, including production static assets.
- Cancellation invalidates pending starts/final results and connection retries, aborts HTTP fallback, rejects pending protocol waiters, and stops microphone tracks even when permission resolves after cancellation. Local regression tests cover cancelling during connection backoff.

Updating: import the same file set from a reviewed upstream commit, reapply these adaptations, update this record, and run host voice unit/browser/gateway tests and both container builds. Do not replace unrelated host changes or fetch a floating branch during builds.

The upstream repository currently has no LICENSE. This snapshot is integrated at the repository owner's request; a license must be selected before publishing it as a public distribution.
