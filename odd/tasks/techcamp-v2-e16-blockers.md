# TechCamp v2 — E16 Blockers Assessment (pre-implementation)

## Objective

Assess what blocks epic **E16 "Simulador y escenarios"** from `docs/10-dag.md:76`
("Los escenarios A–E corren con un comando, con fixtures de clima grabados, y su
bloque expected pasa como prueba end-to-end en CI (06 §10)", deps E4, E5, E7)
**before** any E16 source code is written, and fix the decisions that would
otherwise force rework mid-epic.

This document is the pre-E16 assessment. The E16 feature doc
(`odd/tasks/techcamp-v2-e16-simulator.md`) is itself a task (T0 below) and is
the gate AGENTS.md imposes before code.

Sources: docs sweep (docs are the source of truth, AGENTS.md rule 1),
repo-state spot-checks (one path each, no broad re-exploration), live
issue-tracker numbers from the parent exploration. No source file changed by
this assessment, so no review candidate was opened.

## Verdict

**E16 can start. 0 OPEN issues block it outright.** Every hard blocker is an
unfiled gap in the repo or an undecided T0 item in the docs — the same shape as
E12 (which started with 0 blocking issues and 6 doc decisions). Six decisions
(D-T16.1 … D-T16.6) are cheap to take now and expensive after the scenario
library, the `/dev/scenarios` route, or the Playwright harness exists.

The closest tracked issue is **#214** (seminar stack bootstrap: no org creation,
minio-init stall) — it kills the one-command acceptance if left unabsorbed, so
T0 scopes it explicitly (D-T16.5).

## Blockers — resolve before or with the first code commit

| # | Blocker | Evidence | Smallest unblocking action |
|---|---|---|---|
| B1 | No E16 feature doc; `odd/tasks/` has none for E16 | `odd/tasks/` listing; AGENTS.md workflow | Create `techcamp-v2-e16-simulator.md` (T0) |
| B2 | `POST /dev/scenarios/{name}:load → 202` is specified in docs but has no implementation: the contract exists, the route does not | `docs/04-api.md:263` specifies it; `server/src/techcamp/simulator/provision.py:4` references it as future work; no router serves `/dev/scenarios` | Decide the contract shape (D-T16.2) and build it in T3 |
| B3 | Path conflict: docs say the scenario library lives at `firmware/simulator/scenarios/<nombre>.yaml`, code has no `firmware/` dir — the simulator is `server/src/techcamp/simulator/` | `docs/06-diseno-detallado.md:421`; `ls firmware` → missing; `server/src/techcamp/simulator/` holds `runner.py`, `trajectory.py`, `publisher.py`, `provision.py` | Decide the library home (D-T16.1) in T0 before T1 writes YAMLs to the wrong place |
| B4 | No e2e harness and no Playwright anywhere: `expected`-as-e2e-in-CI (docs/06:485) has nothing to run on | `ls web/e2e` → missing; no Playwright dep in `web/`; AGENTS.md: Playwright arrives with E16 | Decide harness + `expected` schema (D-T16.4) in T0, build in T5 |
| B5 | #214 seminar stack bootstrap (no org creation, minio-init stall) kills the one-command acceptance (`sim run --scenario …` per docs/06:408) | Issue #214 OPEN | Absorb into T0 scope decision (D-T16.5): one-command bootstrap owns org/bucket creation or files it out |
| B6 | `sim record-weather` exists only as one docs line; no fixture format, no versioning rule, no recorded fixture on disk | `docs/06-diseno-detallado.md:489` (single bullet); no `record_weather` in `server/src` | Decide fixture format + record-once rule (D-T16.3) in T0 before T1/T3 |

Correction to the parent handoff: the exploration brief said "`POST
/dev/scenarios/{name}:load` (docs/04:263 zero hits)". Spot-check shows
**one hit** — `docs/04-api.md:263` specifies the endpoint. The gap is
implementation (no route serves it), not specification. B2 above reflects the
corrected shape.

## Degraded — E16 proceeds, with a documented limitation

| # | Degradation | Evidence | Handling |
|---|---|---|---|
| D1 | Timescale teardown flakes in CI | #89 OPEN | E16's e2e retries scenario setup; flakes recorded, not fixed here |
| D2 | Web flaky clock (`CalibrationSheet.test.tsx:81`, 60 s tolerance missed by 69 ms) | #292 OPEN | Playwright waits on `expected` state, never on wall-clock arithmetic |
| D3 | Manual push path: assert `/dev/outbox` instead of a real browser push | #146 OPEN | Scenario A asserts the push via `/dev/outbox` (`docs/04:268`); no real browser push in scope |
| D4 | Simulator gaps follow-up | #215 OPEN | T2 absorbs what #215 tracks; closes or narrows it |
| D5 | Risk `low` follow-up (baseline serves `thresholds={}`, every prediction `low`) | #277 OPEN; D-T9.2 | Scenarios assert the served risk signal as-is; no calibration invented |
| D6 | Open-Meteo follow-ups bypassed via recorded fixtures | Parent exploration | `sim record-weather` runs once (D-T16.3); CI never hits live Open-Meteo |

## Contract conflicts to settle in docs (docs win, AGENTS.md rule 2)

| # | Conflict | Line A | Line B |
|---|---|---|---|
| C1 | Scenario library path: `firmware/simulator/scenarios/` vs the real module `server/src/techcamp/simulator/` | `docs/06-diseno-detallado.md:421` | Repo layout (no `firmware/` dir) |
| C2 | `POST /dev/scenarios/{name}:load` route shape unspecified beyond `→ 202`: request body, idempotency, response | `docs/04-api.md:263` (one line) | `docs/04-api.md:266` (the `/dev/jobs` paragraph documents `day` semantics; scenarios have none) |
| C3 | One-command bootstrap scope: `sim run` "crear org, finca, parcela, ciclo y nodos (claim) si no existen" (docs/06:409) vs #214 (no org creation in bootstrap) | `docs/06-diseno-detallado.md:409` | #214 OPEN |
| C4 | Fixture provenance: "se graban una vez desde Open-Meteo y quedan versionados" — synthetic vs recorded, who versions where | `docs/06-diseno-detallado.md:489` | No fixture dir, no version rule anywhere |
| C5 | D / E8 boundary: scenario D (offline sync) replays E8's airplane-mode path — E16 asserts it, does not rebuild it | `docs/06:473-480` table row D | E8 delivered to `main` |
| C6 | TDD per behavior change vs docs-only T0 with no runnable RED | `AGENTS.md:100` | This assessment + T0 write no source |

Resolution for C6: this assessment writes no source and therefore runs **no**
pytest. T0 likewise writes only docs. For E16's code tasks the repo rule stands
— RED → GREEN → REFACTOR with tests next to the behavior, one `just gate
<paths…>` per work unit, `gate-full` only at epic close (T8).

## Already in place — reuse, do not rebuild

| Asset | Evidence |
|---|---|
| Single-node sine simulator: backfill + live loops, one uplink per timestamp, `seq = i + 1` | `server/src/techcamp/simulator/runner.py`, `trajectory.py`, `publisher.py`, `provision.py`, `__main__.py` (`--backfill-days`, `--live`; no `--scenario` yet) |
| Ingestor: dedupe + calibration, raw ADC contract | Ingestor path; docs/06:484 (sim sends raw ADC, per-node calibration) |
| MQTT contract + SSE fan-out | `docs/04-api.md` MQTT contract; `GET /api/v1/stream` |
| Weather `upsert_daily` hook + `/dev/jobs/{name}:run` (`day?` semantics) | `docs/04-api.md:261,266` |
| FAO-56 rainfed path (`K = 0`, no-sensor balance) | Irrigation module; docs/06:450 |
| Alert evaluators: water / heat / fungal / node_offline / flood / drought | Alerts module; docs/06 table A–E |
| E8 delivered to `main`: offline sync is done, scenario D asserts it | `main` history |
| E16 deps delivered: E4 (telemetry), E5 (weather), E7 (alerts) | `docs/10-dag.md:76` deps satisfied |

## Library facts (Context7)

Not re-run. E16's stack needs no new external API verification at assessment
time: MQTT/Mosquitto, Open-Meteo (record-once via existing client pattern),
and Playwright (T0 decision D-T16.4, verified with `find-docs` when T5 starts)
are all previously covered patterns. If T5's harness needs a Playwright API
beyond the standard install + config, the lane verifies it with `find-docs`
then and records the result — no invented API surface either way.

## Issue-tracker sweep (numbers from parent exploration, live)

Parent ran the sweep; this assessment did not re-run `gh issue list`.
Recorded here so the plan cites them:

| Issue | State | Bucket | Note |
|---|---|---|---|
| #214 | OPEN | BLOCKER-adjacent | Seminar stack bootstrap (no org creation, minio-init stall) kills one-command acceptance → D-T16.5 |
| #89 | OPEN | DEGRADED | Timescale teardown flakes → D1 |
| #292 | OPEN | DEGRADED | Web flaky clock → D2 (never assert wall-clock) |
| #146 | OPEN | DEGRADED | Manual push → assert `/dev/outbox` (D3) |
| #215 | OPEN | DEGRADED | Simulator gaps follow-up → absorbed by T2 (D4) |
| #277 | OPEN | DEGRADED | Risk `low` follow-up → assert served signal (D5) |
| Open-Meteo follow-ups | mixed | BYPASSED | Recorded fixtures; CI offline (D6) |
| E8 | merged | NOT A BLOCKER | Backend done; scenario D asserts it (C5) |

**0 OPEN issues block E16 outright.** #214 is the closest: untracked as an E16
blocker, decided in T0 (D-T16.5) rather than filed.

## Proposed E16 task plan (needs owner authorization)

| Task | Content | Depends on |
|---|---|---|
| T0 | Six decisions into owner docs (D-T16.1…D-T16.6) + this feature doc | — |
| T1 | Scenario library: YAMLs A–E + normal + weather fixtures (record-once) | T0 |
| T2 | Runner `sim run --scenario` + multi-node + faults + truth export | T1 |
| T3 | `POST /dev/scenarios/{name}:load` + `sim record-weather` | T1, D-T16.2, D-T16.3 |
| T4 | `expected` checker + domain test (`Dr > RAW` opens, not before) | T1, T2 |
| T5 | Playwright harness + CI job running `expected` e2e | T4, D-T16.4 |
| T6 | Scenarios A / B / C / E / normal wired with per-scenario `expected` | T2, T3, T5 |
| T7 | Scenario D (PWA offline) e2e on top of E8 | T5, T6 |
| T8 | Gate + evidence, `gate-full` once, epic close | T1–T7 |

Each task closes with one `just gate <paths…>` run and one RDD receipt per
work-unit commit, chained PRs in order (AGENTS.md, `work-unit-commits`,
`chained-pr`).

## Hard requirements that shape every task (docs sweep, condensed)

Deterministic scenarios with pinned seed (docs/06 §10); raw ADC uplinks with
per-node calibration (docs/06:484); same MQTT contract as a real node
(docs/06:396); `expected` block passes as e2e in CI (docs/06:485); domain test
`water_stress` opens iff `Dr > RAW` (docs/06:486); noiseless trajectory is the
water-balance error baseline (docs/11 §3, docs/06:487); weather fixtures
recorded once from Open-Meteo and versioned, demo offline except the assistant
(docs/06:489); scenario arithmetic A (15.3 %, crossing day 10.4, docs/06:442-448)
and E (rainfed `K = 0`, `water_stress` day 16, critical day 18, docs/06:465-471);
org isolation with `404` (docs/04:17); hexagonal layers (ADR-0002);
`sim run --scenario el-nino --backfill 14d --live` one-command shape
(docs/06:408).

## Undecided in the docs (owner input needed)

1. Scenario library home: `firmware/simulator/scenarios/` vs `server/`-adjacent (C1, D-T16.1).
2. `/dev/scenarios/{name}:load` body + idempotency + response (C2, D-T16.2).
3. Fixture format + versioning: synthetic vs recorded, where hashes live (C4, D-T16.3).
4. Playwright + `expected` schema: harness shape, CI job, assertion grammar (D-T16.4).
5. One-command bootstrap scope vs #214: what `sim run` owns (C3, D-T16.5).
6. Outbox assertion for scenario A push vs real browser push (D-T16.6).
