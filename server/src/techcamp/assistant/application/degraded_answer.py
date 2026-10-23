"""The degraded answer, as a use case (E12 T4; docs/06-diseno-detallado.md §9;
docs/04-api.md §Riesgo, métricas y asistente; D-T12.1).

docs/06 §9 draws the branch the daily limit and the monthly budget fall into:

```
alt límite (10/día) o presupuesto agotado
  API-->>U: respuesta sin LLM: datos de la parcela + documentos relevantes
  API->>API: guardar el mensaje con model, tokens y costo nulos
```

This function is that answer. It is deliberately thin — the facts are already
assembled by `application.plot_facts`, the documents by `application.retrieval`, and
the text by the pure `domain.degraded` — because on this branch nothing may fail
between the decision and the producer's screen: there is no provider to fail, and
the only thing left to do is write down what is already known.

**Its signature takes no `LLMClient`**, and that is the whole point of the shape.
`evaluate_quota` (T1) argued the same thing for the quota decision — "budget
exhausted → zero provider calls" as a structural fact rather than a promise — and
here it is pinned by two tests: one that inspects the signature of every public
function on this path, and one that hands a `FakeLLMClient` to the test and asks it
afterwards how many times it was called. The answer is that no code path from here
can reach a provider, because none of them names one.

What the caller does with the result is T5's and T6's: write the text and the
citations into the response, store the message with `model`, tokens and `cost_cop`
**null** (D-T12.1 — `model IS NULL` is what distinguishes "the LLM was never
called" from "it was called and cost nothing"), and answer `429` with a
`Retry-After` when the day's limit is what stopped it.
"""

from __future__ import annotations

from dataclasses import dataclass

from techcamp.assistant.application.retrieval import RetrievedEvidence
from techcamp.assistant.domain.citations import Citation
from techcamp.assistant.domain.degraded import DegradedAnswer, render_degraded_answer
from techcamp.assistant.domain.facts import PlotFacts

WITHOUT_A_PLOT_TEXT = (
    "La pregunta no nombra ninguna parcela, así que no hay datos de parcela que mostrar. "
    "El asistente responde hoy sin el modelo; los documentos que se relacionan con la "
    "pregunta están en las fuentes citadas."
)
"""The degraded answer of a question that names no plot (docs/06 §9).

A general question has no facts to print, but it still may not reach the provider
when the day or the budget is exhausted. The text says exactly that: no plot, no
facts, and the relevant documents where the endpoint already carries them.
"""


@dataclass(frozen=True, slots=True)
class PlotlessDegradedAnswer:
    """What the endpoint serves for a general question without the model.

    `DegradedAnswer` is not reused because it requires a `plot_id` and a plot's
    facts, and a plotless turn has neither: inventing an id would let the endpoint
    pair one plot's identity with a text about no plot. The citations are the same
    ones retrieval resolved, carried unchanged (ADR-0008:12).
    """

    text: str
    citations: tuple[Citation, ...]


async def answer_without_llm(*, facts: PlotFacts, evidence: RetrievedEvidence) -> DegradedAnswer:
    """The plot's facts and the relevant documents, with no provider call.

    docs/06-diseno-detallado.md §9: "En la respuesta sin LLM se devuelven estos
    mismos hechos junto con los documentos relevantes." So the input is the same
    `PlotFacts` the model would have been given and the same retrieved evidence,
    and the output is the same citations — carried **unchanged**, so the endpoint
    writes exactly the sources retrieval resolved and a producer can open them
    (ADR-0008:12).

    Reads only `evidence.citations` and never `evidence.fragments`: the fragments
    are the text T5 puts **in** a prompt, and on this branch there is no prompt and
    no call to put one in.

    `async` because it is one step of a request's use-case sequence
    (T5: quota → facts → retrieval → this or the call), not because anything here
    awaits: the renderer is pure and the value is built by it.
    """
    return render_degraded_answer(facts=facts, citations=evidence.citations)


async def answer_without_a_plot(*, evidence: RetrievedEvidence) -> PlotlessDegradedAnswer:
    """The degraded answer when `_prepare` resolved no plot (`facts is None`).

    The same branch as `answer_without_llm`, for the question whose conversation
    names no plot: there is nothing to render from `PlotFacts`, and there is still
    no provider call to make. The citations travel unchanged, so the endpoint's
    `sources` frame is the retrieval's answer either way.
    """
    return PlotlessDegradedAnswer(text=WITHOUT_A_PLOT_TEXT, citations=evidence.citations)
