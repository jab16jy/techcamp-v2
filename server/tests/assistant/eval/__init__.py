"""The assistant's evaluation harness (E12 T8; ADR-0007:15).

The harness replays the **deterministic half** of the assistant flow and stops
before the provider: the closed question set, the corpus the answers would come
from, the fragments each question retrieves and the exact prompt the provider
would receive. It exists to hand a human agronomist precisely what must be
graded — accuracy, citations and clarity, per ADR-0007:15 — and it grades
nothing.

Three invariants hold over the whole package, and `tests/assistant/eval/` asserts
each of them:

* **No provider.** No module here reaches an `LLMClient`, not even through a test
  double. The replay calls the real `application.retrieval.retrieve_fragments`
  and the real `adapters.api.prompt.build_prompt`, and stops.
* **No verdict.** No score, grade, threshold, pass or fail field exists on
  `ReplayReport` or on anything it carries. Those are owner work — the pass
  threshold is an open owner decision in the E12 feature doc — and a structural
  guard asserts their absence rather than trusting nobody adds one.
* **Pinned bytes.** `corpus_pin` records which corpus files were replayed, by
  digest, so a run can be reproduced from the pin alone.
"""
