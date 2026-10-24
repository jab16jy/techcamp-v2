"""In-memory doubles for the assistant's six ports, and for the foreign
repositories its fact builder takes (E12 T1, E12 T4).

They stand in for the adapters T2/T3 build, and they are held to the same rules
those adapters are: the knowledge repository filters by `similarity_floor` and cuts
at `top_k`, the transcript answers only to the owner of a conversation, and the
quota counts questions in **America/Bogota** rather than in UTC (docs/06 §9). A
double that agreed with everything would let a use case pass a test and fail in
production, so each one refuses the shape the real thing refuses.

E12 T4 appends the doubles for the **foreign** repositories — `farms`,
`identity`, `telemetry`, `alerts`, `irrigation` and `risk` — which the
assistant reaches only through those modules' public `application` facades
(docs/05-arquitectura.md:150-151). They implement the methods that path calls and
no others, and each one carries the rule the real repository holds: a plot of
another org is absent, a metric without a reading is `None`, a resolved alert does
not come back, and a recommendation that was never calculated raises.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import AsyncIterator, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from techcamp.alerts.domain.models import Alert, AlertState, Severity
from techcamp.assistant.application.ports import (
    EMBEDDING_DIMENSION,
    KnowledgeDocument,
    RetrievedFragment,
    StreamToken,
    TokenUsage,
)
from techcamp.assistant.domain.errors import ConversationNotFoundError
from techcamp.assistant.domain.limits import (
    budget_exhausted,
    daily_limit_exhausted,
    month_spend_cop,
)
from techcamp.assistant.domain.models import ChatMessage, Conversation, Message, MessageRole
from techcamp.farms.domain.models import Plot
from techcamp.identity.domain.models import Membership
from techcamp.irrigation.domain.models import IrrigationRecommendation
from techcamp.risk.domain.models import EventType, ModelVersion, RiskPrediction
from techcamp.shared.dates import BOGOTA_TZ
from techcamp.telemetry.domain.models import ReadingPoint

DEFAULT_MODEL = "deepseek-chat"
"""The provisional provider of ADR-0026, so a test reads the same model name a real
stream would report."""


class FakeLLMClient:
    """An `LLMClient` that answers a fixed text and records what it was asked.

    The record is the point: "one call per request" (ADR-0007:26) is only
    observable if the double counts the calls, and the send-list test of D-T12.6 is
    only meaningful if the messages it sent are still readable afterwards.
    """

    def __init__(
        self,
        *,
        text: str = "Respuesta del doble.",
        model: str = DEFAULT_MODEL,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        tokens: Sequence[str] | None = None,
    ) -> None:
        self._tokens = list(tokens) if tokens is not None else [text]
        self._model = model
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.messages: list[Sequence[ChatMessage]] = []

    @property
    def call_count(self) -> int:
        return len(self.messages)

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamToken]:
        self.messages.append(list(messages))
        for index, text in enumerate(self._tokens):
            is_last = index == len(self._tokens) - 1
            yield StreamToken(
                text=text,
                usage=self._usage() if is_last else None,
                model=self._model if is_last else None,
            )

    def _usage(self) -> TokenUsage | None:
        if self._prompt_tokens is None or self._completion_tokens is None:
            return None
        return TokenUsage(
            prompt_tokens=self._prompt_tokens, completion_tokens=self._completion_tokens
        )


class FakeEmbedder:
    """An `Embedder` whose vectors are deterministic and of the pinned width.

    The bytes of a SHA-256 digest spread over the unit sphere: the same text always
    embeds to the same vector (so a test can store a fragment and retrieve it), a
    different text to a different one (so a test can prove the floor excludes an
    unrelated fragment), and the width is ADR-0026's.
    """

    def __init__(self, *, dimension: int = EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        if self.dimension != EMBEDDING_DIMENSION:
            raise ValueError(
                f"ADR-0026 pins {EMBEDDING_DIMENSION} dimensions, got {self.dimension}"
            )
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 384 floats out of 32 bytes: the digest is stretched, and every byte maps
        # to [-1, 1] so the vector has no direction of its own.
        stretched = (digest * ((self.dimension // len(digest)) + 1))[: self.dimension]
        vector = [value / 127.5 - 1.0 for value in stretched]
        norm = math.sqrt(sum(component * component for component in vector))
        return [component / norm for component in vector]


def fragment_vector_at(similarity: float, *, query: Sequence[float] = (1.0, 0.0)) -> list[float]:
    """A chunk vector whose cosine against `query` is exactly `similarity`.

    Retrieval compares by cosine, so a test that needs a 0.74 or a 0.95 builds a
    vector with that score rather than asserting on a number a stub made up.
    """
    query_norm = math.sqrt(sum(component * component for component in query))
    first = similarity * query_norm
    second = math.sqrt(max(0.0, query_norm**2 - first**2))
    return [first, second]


class _Chunk:
    """A stored `kb_chunk` of the double: the document it belongs to, its text and
    the vector the real store would compare with cosine."""

    def __init__(self, *, document_id: UUID, content: str, embedding: Sequence[float]) -> None:
        self.document_id = document_id
        self.content = content
        self.embedding = list(embedding)


class FakeKnowledgeRepository:
    """A `KnowledgeRepository` over fragments held in memory.

    The similarity of every candidate is the real cosine the HNSW
    `vector_cosine_ops` index uses (docs/03-modelo-datos.md §Índices principales),
    so `similarity_floor` and `top_k` mean here what they will mean against
    pgvector. A vector with no direction scores `0.0` rather than dividing by zero,
    so it can never sneak past the floor.

    E12 T4 added two things and changed no other: the `ignore_similarity_floor`
    flag below, and `latest_climate_bulletin`. Every T1 test keeps the double it
    had, because both additions default to what the constructor did before.
    """

    def __init__(self, *, ignore_similarity_floor: bool = False) -> None:
        self._documents: dict[UUID, KnowledgeDocument] = {}
        self._chunks: list[_Chunk] = []
        self._ignore_floor = ignore_similarity_floor
        self.calls: list[tuple[Sequence[float], int, float]] = []
        """`ignore_similarity_floor` makes this double answer with whatever the
        query finds, floor or no floor. It is how the **defensive re-filter** of
        `application.retrieval` is testable: an adapter that forgot the floor would
        contribute a 0.74 fragment, and the use case has to refuse it without
        trusting the port to have done what its docstring says. `False` by default,
        so a test that does not say otherwise gets the honest repository."""

    def add_document(
        self,
        *,
        document_id: UUID,
        title: str,
        kind: str,
        source_url: str | None = None,
        license: str | None = None,
        published_on: date | None = None,
        department: str | None = None,
        enso_state: str | None = None,
    ) -> KnowledgeDocument:
        document = KnowledgeDocument(
            id=document_id,
            title=title,
            kind=kind,
            source_url=source_url,
            license=license,
            published_on=published_on,
            department=department,
            enso_state=enso_state,
        )
        self._documents[document_id] = document
        return document

    def add_fragment(self, *, document_id: UUID, content: str, embedding: Sequence[float]) -> None:
        self._chunks.append(_Chunk(document_id=document_id, content=content, embedding=embedding))

    async def search_by_embedding(
        self, embedding: Sequence[float], *, top_k: int, similarity_floor: float
    ) -> list[RetrievedFragment]:
        self.calls.append((list(embedding), top_k, similarity_floor))
        floor = -1.0 if self._ignore_floor else similarity_floor
        scored = [
            (_chunk, similarity)
            for _chunk in self._chunks
            if (similarity := _cosine(embedding, _chunk.embedding)) >= floor
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [
            RetrievedFragment(
                id=uuid4(),
                document_id=chunk.document_id,
                content=chunk.content,
                similarity=similarity,
            )
            for chunk, similarity in scored[:top_k]
        ]

    async def get_document(self, document_id: UUID) -> KnowledgeDocument | None:
        return self._documents.get(document_id)

    async def latest_climate_bulletin(self, *, department: str | None) -> KnowledgeDocument | None:
        """The newest bulletin of `department`, else the newest national one.

        E12 T4, the port added to `KnowledgeRepository` on top of T1's shape: the
        regional bulletin first, the national one as docs/06 §9's own fallback,
        and `None` when the corpus holds neither — an empty corpus is missing
        evidence, not an error.

        **Only bulletins, and only the ones that carry a publication date.** A
        document of another `kind` is not the seasonal source, and a bulletin with
        no `published_on` cannot be the *latest* of anything: ordering it against
        dated ones would mean comparing a date with nothing, so it is not
        returned at all rather than returned as though it were current.
        """
        bulletins = [
            document
            for document in self._documents.values()
            if document.kind == "agroclimatic_bulletin" and document.published_on is not None
        ]
        regional = [
            bulletin
            for bulletin in bulletins
            if department is not None and bulletin.department == department
        ]
        if regional:
            return max(regional, key=_bulletin_order)
        national = [bulletin for bulletin in bulletins if bulletin.department is None]
        if national:
            return max(national, key=_bulletin_order)
        return None


class FakeConversationRepository:
    """A `ConversationRepository` that only ever answers to the owner (D-T12.5)."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, Conversation] = {}

    async def create(self, *, user_id: UUID, plot_id: UUID | None) -> Conversation:
        conversation = Conversation(id=uuid4(), user_id=user_id, plot_id=plot_id)
        self._by_id[conversation.id] = conversation
        return conversation

    async def get_for_user(self, *, user_id: UUID, conversation_id: UUID) -> Conversation | None:
        conversation = self._by_id.get(conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        return conversation

    async def list_for_user(self, *, user_id: UUID) -> list[Conversation]:
        return [c for c in self._by_id.values() if c.user_id == user_id]


class FakeMessageRepository:
    """A `MessageRepository` scoped by the owning conversation.

    It takes the conversation store because ownership lives on `conversation`
    (docs/03-modelo-datos.md:363-366): `message` has no `user_id` of its own, so a
    store that did not resolve the owner could serve a transcript to anyone holding
    an id — the 404-vs-leak case docs/09 §Seguridad forbids.
    """

    def __init__(self, *, conversations: FakeConversationRepository) -> None:
        self.conversations = conversations
        self._by_conversation: dict[UUID, list[Message]] = {}

    async def add(self, *, user_id: UUID, conversation_id: UUID, message: Message) -> Message:
        await self._require_visible(user_id=user_id, conversation_id=conversation_id)
        self._by_conversation.setdefault(conversation_id, []).append(message)
        return message

    async def list_for_user(self, *, user_id: UUID, conversation_id: UUID) -> list[Message]:
        if (
            await self.conversations.get_for_user(user_id=user_id, conversation_id=conversation_id)
            is None
        ):
            return []
        return list(self._by_conversation.get(conversation_id, []))

    async def set_helpful(self, *, user_id: UUID, message_id: UUID, helpful: bool) -> bool:
        for conversation_id, messages in self._by_conversation.items():
            if (
                await self.conversations.get_for_user(
                    user_id=user_id, conversation_id=conversation_id
                )
                is None
            ):
                continue
            for index, message in enumerate(messages):
                if message.id != message_id:
                    continue
                # Rebuilt through the domain, so a `helpful` on a user message is
                # refused here exactly as the column's CHECK refuses it.
                messages[index] = Message(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                    evidence=message.evidence,
                    helpful=helpful,
                )
                return True
        return False

    async def _require_visible(self, *, user_id: UUID, conversation_id: UUID) -> None:
        if (
            await self.conversations.get_for_user(user_id=user_id, conversation_id=conversation_id)
            is None
        ):
            raise ConversationNotFoundError(conversation_id)


class FakeUsageQuota:
    """A `UsageQuotaPort` over the turns a `MessageRepository` holds, plus the
    questions it has admitted.

    Both reads resolve the day and the month in **America/Bogota**
    (docs/06-diseno-detallado.md §9), which is the whole reason this is not a
    `date(created_at)`: 2026-10-07T02:00Z is still the 6th for the quota, and a
    question counts whether it was answered by the model or degraded without it.

    The reservations are what `admit_question` records, and a count that read
    `questions_asked_on` includes them: a real adapter's reservation is the write
    that makes the count true, so a question that was admitted but not yet
    answered is still a question of that day.
    """

    def __init__(self, *, messages: FakeMessageRepository) -> None:
        self.messages = messages
        self._reserved: list[tuple[UUID, date]] = []

    async def questions_asked_on(self, *, user_id: UUID, day: date) -> int:
        stored = sum(
            1
            for message in await self._visible(user_id=user_id)
            if message.role is MessageRole.USER and _local(message.created_at).date() == day
        )
        return stored + sum(
            1
            for reserved_user, reserved_day in self._reserved
            if (reserved_user, reserved_day) == (user_id, day)
        )

    async def month_costs_cop(self, *, user_id: UUID, month: date) -> list[Decimal | None]:
        return [
            message.evidence.cost_cop
            for message in await self._visible(user_id=user_id)
            if (_local(message.created_at).year, _local(message.created_at).month)
            == (month.year, month.month)
        ]

    async def admit_question(
        self, *, user_id: UUID, day: date, month: date, budget_cap_cop: Decimal
    ) -> bool:
        """Reserve the question, or refuse it without recording anything.

        The refusal is decided before the reservation is appended, which is the
        whole point of the atomic admission (R3-QUOTA-RACE): two callers that both
        arrived at the ninth question cannot both be served the tenth, because the
        second one counts the first one's reservation.
        """
        asked_today = await self.questions_asked_on(user_id=user_id, day=day)
        spend = month_spend_cop(await self.month_costs_cop(user_id=user_id, month=month))
        if daily_limit_exhausted(asked_today) or budget_exhausted(spend, budget_cap_cop):
            return False
        self._reserved.append((user_id, day))
        return True

    async def _visible(self, *, user_id: UUID) -> list[Message]:
        visible: list[Message] = []
        for conversation in await self.messages.conversations.list_for_user(user_id=user_id):
            visible.extend(
                await self.messages.list_for_user(user_id=user_id, conversation_id=conversation.id)
            )
        return visible


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity of two vectors, `0.0` when either has no direction."""
    if len(left) != len(right):
        raise ValueError(f"vector widths differ: {len(left)} and {len(right)}")
    left_norm = math.sqrt(sum(component * component for component in left))
    right_norm = math.sqrt(sum(component * component for component in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)


def _local(instant: datetime) -> datetime:
    return instant.astimezone(BOGOTA_TZ)


# ---------------------------------------------------------------------------
# E12 T4: the foreign ports `application.plot_facts` takes.
#
# These stand in for the repositories of `farms`, `identity`, `telemetry`,
# `alerts`, `irrigation` and `risk`, which the assistant reaches only through
# their public `application` facades (docs/05-arquitectura.md:150-151). Each one
# implements the methods that path calls and refuses the shape the real thing
# refuses: a plot of another org is absent rather than forbidden, a metric with no
# reading in the window is `None` rather than `0.0`, an alert that is resolved
# does not come back, and a recommendation that was never calculated raises
# instead of answering a zero depth.
# ---------------------------------------------------------------------------


def _bulletin_order(bulletin: KnowledgeDocument) -> tuple[date, str]:
    """Ordering key for "the latest bulletin". The title breaks a tie between two
    documents of the same date, so the answer is the same bulletin whatever order
    the corpus was stored in."""
    assert bulletin.published_on is not None, "only dated bulletins are ordered"
    return (bulletin.published_on, bulletin.title)


class FakePlotRepository:
    """A `farms` `PlotRepository` over plots held in memory.

    **Implements `get_for_orgs` and nothing else**, which is the only method
    `resolve_plot_access` calls — the access gate every plot-scoped read in the
    system uses (docs/09-cuellos-de-botella.md §Seguridad). That one method is
    also where this double earns its place: a plot whose `org_id` is not among
    the caller's orgs answers `None`, which `resolve_plot_access` turns into
    `PlotNotFoundError`, so **a plot of another org and an absent plot are the
    same 404** (docs/04-api.md §Convenciones HTTP) rather than a `403` that would
    confirm the plot exists.
    """

    def __init__(self, plots: Sequence[Plot] = ()) -> None:
        self._plots = list(plots)

    def add(self, plot: Plot) -> Plot:
        self._plots.append(plot)
        return plot

    async def get_for_orgs(self, plot_id: UUID, org_ids: Sequence[UUID]) -> Plot | None:
        for plot in self._plots:
            if plot.id == plot_id and plot.org_id in org_ids:
                return plot
        return None


class FakeMembershipRepository:
    """An `identity` `MembershipRepository` over memberships held in memory.

    `resolve_plot_access` reads `list_for_user` and turns the org ids into the set
    the plot repository is asked about, so the org-isolation tests are decided
    here: a user with one membership cannot reach a plot of another org, and the
    plot repository's answer above is the same `None` either way.
    """

    def __init__(self, memberships: Sequence[Membership] = ()) -> None:
        self._memberships = list(memberships)

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        return [m for m in self._memberships if m.user_id == user_id]

    async def list_for_org(self, org_id: UUID) -> list[Membership]:
        return [m for m in self._memberships if m.org_id == org_id]

    async def get(self, user_id: UUID, org_id: UUID) -> Membership | None:
        for membership in self._memberships:
            if membership.user_id == user_id and membership.org_id == org_id:
                return membership
        return None


class FakeReadingRepository:
    """A `telemetry` `ReadingRepository` over one reading per metric.

    **Implements `query_latest_valid_by_metric`**, the read
    `query_latest_plot_readings` is a wrapper over (D-T0.4). The 24-hour window is
    applied **here** so the facade's own contract is what the tests exercise: a
    reading outside `[now - 24h, now]` is not returned at all, and a metric with
    nothing inside the window comes back absent, which the facade maps to `None`
    — never to `0.0`.

    `latest_valid_by_metric` records the metrics it was asked for and the `now` it
    was given, so a test can assert the assistant asks the facade for exactly
    `ASSISTANT_METRICS` rather than for every metric the glossary names.
    """

    def __init__(self, points: Sequence[tuple[str, ReadingPoint]] = ()) -> None:
        self._points = list(points)
        self.latest_valid_by_metric_calls: list[tuple[Sequence[str], datetime]] = []

    def add(self, metric: str, point: ReadingPoint) -> None:
        self._points.append((metric, point))

    async def query_latest_valid_by_metric(
        self,
        plot_id: UUID,
        org_id: UUID,
        *,
        metrics: Sequence[str],
        now: datetime,
    ) -> dict[str, ReadingPoint]:
        self.latest_valid_by_metric_calls.append((list(metrics), now))
        window_start = now - timedelta(hours=24)
        newest: dict[str, ReadingPoint] = {}
        for metric, point in self._points:
            if metric not in metrics or not window_start <= point.time <= now:
                continue
            current = newest.get(metric)
            if current is None or point.time > current.time:
                newest[metric] = point
        return newest


class FakeAlertRepository:
    """An `alerts` `AlertRepository` over alerts held in memory.

    **Implements `list_open_for_plots`**, the read behind
    `list_open_alerts_for_plots` (D-T0.3): `state <> 'resolved'`, scoped by
    `plot_ids` **and** `org_ids`, ordered critical first and then newest first.
    The order is the facade's promise, so the facts it assembles are ordered the
    same way on every question.

    A resolved alert is not returned even when it is in the list, which is what
    makes "the producer closed it" observable in a test.
    """

    def __init__(self, alerts: Sequence[Alert] = ()) -> None:
        self._alerts = list(alerts)

    def add(self, alert: Alert) -> Alert:
        self._alerts.append(alert)
        return alert

    async def list_open_for_plots(
        self, plot_ids: Sequence[UUID], org_ids: Sequence[UUID]
    ) -> list[Alert]:
        wanted = set(plot_ids)
        visible = [
            alert
            for alert in self._alerts
            if alert.plot_id in wanted
            and alert.org_id in set(org_ids)
            and alert.state is not AlertState.RESOLVED
        ]
        return sorted(
            visible,
            key=lambda alert: (alert.severity is not Severity.CRITICAL, -_epoch(alert.opened_at)),
        )


class FakeIrrigationRecommendationRepository:
    """An `irrigation` `IrrigationRecommendationRepository` over rows held in
    memory.

    **Implements `get_for_plot`**, the read `query_plot_recommendation` wraps: a
    missing row answers `None` and the facade raises `RecommendationNotFoundError`
    from it (irrigation/application/query_irrigation.py:77-79), which is the path
    the fact builder has to survive. `missing=True` is that path — the flag
    configures the **repository**, not an exception in the double, because the
    real repository never raises and a double that did would test a shape the
    adapter does not have.

    `org_id` is honoured, so a recommendation of another org is absent rather than
    served.
    """

    def __init__(
        self, recommendations: Sequence[IrrigationRecommendation] = (), *, missing: bool = False
    ) -> None:
        self._recommendations = list(recommendations)
        self._missing = missing

    async def get_for_plot(
        self, plot_id: UUID, day: date, org_id: UUID | None = None
    ) -> IrrigationRecommendation | None:
        if self._missing:
            return None
        for recommendation in self._recommendations:
            if recommendation.plot_id == plot_id and recommendation.day == day:
                return recommendation
        return None


class FakeRiskRepository:
    """A `risk` `RiskRepository` over versions and predictions held in memory.

    **Implements `served_version` and `latest_prediction`**, the two reads
    `query_plot_risk` makes per event. `served_version` answers `None` for an
    event with no registered version, which is how `drought` behaves until M3
    exists (D-T9.3) and how the use case decides that event is simply absent
    rather than predicted at `0`.
    """

    def __init__(
        self,
        *,
        versions: Sequence[ModelVersion] = (),
        predictions: Sequence[RiskPrediction] = (),
    ) -> None:
        self._versions = list(versions)
        self._predictions = list(predictions)

    async def served_version(self, name: str) -> ModelVersion | None:
        for version in self._versions:
            if version.name == name:
                return version
        return None

    async def latest_prediction(
        self, cell_id: int, event_type: EventType, model_version_id: UUID
    ) -> RiskPrediction | None:
        candidates = [
            prediction
            for prediction in self._predictions
            if prediction.cell_id == cell_id
            and prediction.event_type is event_type
            and prediction.model_version_id == model_version_id
        ]
        if not candidates:
            return None
        # Newest `created_at`, then `id`: the same tie-break the real repository
        # uses, so "the latest" means one row and not whichever was inserted last.
        return max(candidates, key=lambda prediction: (prediction.created_at, prediction.id))


def _epoch(instant: datetime) -> float:
    """`instant` as a sortable number, so a descending order reads as one
    expression instead of a negated key."""
    return instant.timestamp()
