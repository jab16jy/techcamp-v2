"""What one call to the model costs (E12 T5; ADR-0026; D-T12.1, D-T12.3;
docs/06-diseno-detallado.md §9 "Qué se guarda de cada turno"; docs/02:78).

`message.cost_cop` is one of the four evidence columns D-T12.1 makes move
together: `model`, the two token counts and `cost_cop` are all reported or all
null. So a call that produced tokens **must** be able to produce a cost, which
means this module needs a price.

**The price is injected, not written here.** ADR-0026 left the real prices to
T8, and ADR-0028 delivered them: `deepseek-flash` at the conservative peak
cache-miss rates converted to COP at the decision day's TRM, wired into
`deps.get_prices` and written back into docs/02:78. A number written here would
be a number no document owns, and it would silently become the budget's basis
(docs/11-metricas.md:132 sums this column).

So:

* `ModelPrice` is the **rate card** of one model, in COP per million tokens.
* `cost_cop` is the arithmetic of docs/02:78 — `tokens × precio_por_millón` —
  quantized once, to the six decimals of `numeric(12,6)`.
* `ModelPrice.local` is the seminar stand-in's card: **zero**, which D-T12.1
  accepts for a call that happened to come free, and which is the truth for a
  model that runs inside the process (ADR-0021: the seminar profile is local).
* **A model with no price is an error, never a zero.** `price_for` raises
  `UnknownModelPriceError` instead of returning a free card: an unmeasured cost
  recorded as `0` is a figure the system would be reporting to a producer's
  budget as though it had measured it.

The wiring owns which card applies (D-T12.3: the seminar profile's USD 5 total
vs the production monthly cap), which is why nothing in this module reads a
setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from techcamp.assistant.application.ports import TokenUsage

MILLION = Decimal(1_000_000)
"""The divisor docs/02:78 states: prices are per million tokens."""

COST_QUANTUM = Decimal("0.000001")
"""`message.cost_cop` is `numeric(12,6)` (docs/03 §Costo y tiempo), so the cost is
rounded to six decimals here — once — and the stored value is the number this
function returned rather than a second rounding of it."""

PriceTable = Mapping[str, "ModelPrice"]
"""Which card answers for which model. One table per profile, supplied by the
wiring that knows the profile (D-T12.3)."""


class UnknownModelPriceError(Exception):
    """Raised when a model answered but no price of it is configured.

    Never answered with a zero: D-T12.1's `cost_cop = 0` means "this call came
    free", and a missing price is a gap in the wiring, not a free call.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f"no configured price for model {model!r}: the cost of that call cannot be "
            "computed, and a zero would be an unmeasured figure"
        )


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """One model's rate card, in COP per million tokens.

    COP, not USD, because the column is `cost_cop` (docs/03:368-376) and the
    budget metric is `SUM(message.cost_cop)` (docs/11:132). Whoever fills this
    in owns the USD→COP conversion, and ADR-0026 leaves the real numbers to T8.
    """

    model: str
    input_cop_per_million: Decimal
    output_cop_per_million: Decimal

    @classmethod
    def local(cls, model: str) -> ModelPrice:
        """A card for a model that runs inside the process and bills nothing.

        The seminar stand-in is the case this exists for (ADR-0021: the seminar
        profile runs locally, with no provider and no monitoring): the call is
        real, the tokens are real, and the cost is zero pesos.
        """
        return cls(model=model, input_cop_per_million=Decimal(0), output_cop_per_million=Decimal(0))


def price_for(table: PriceTable, model: str) -> ModelPrice:
    """The card for `model`, or `UnknownModelPriceError`.

    A model that answers and is not in the table is a wiring gap: the profile's
    provider changed, or its keys were configured without its prices.
    """
    try:
        return table[model]
    except KeyError:
        raise UnknownModelPriceError(model) from None


def cost_cop(usage: TokenUsage, price: ModelPrice) -> Decimal:
    """What `usage` cost at `price`, in pesos.

    The formula is docs/02:78's: input tokens at the input rate plus output
    tokens at the output rate, both per million. Two rates and not one blended
    one because a provider's output is several times its input, and a single
    average rate would report a figure the provider never charged.
    """
    total = (
        Decimal(usage.prompt_tokens) * price.input_cop_per_million
        + Decimal(usage.completion_tokens) * price.output_cop_per_million
    ) / MILLION
    return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
