"""Orchestrate CAG stress scenarios and emit CSV + REPORT.md.

Usage::

    uv run python -m evals.stress.run --http http://localhost:8000 \\
        --scenarios growing,pivot,contradiction \\
        --attachment-sizes 0,5,20,50,100 \\
        --repeats 3 \\
        --output evals/stress/results.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.dependencies import get_estimation_service, get_session_store
from app.main import app
from app.schemas.estimation import EstimationResult
from app.schemas.observation import TurnObservation
from app.services.llm_wrapper import _estimate_cost
from app.sessions.models import ProjectMetadata
from app.sessions.store import SessionStore
from evals.stress.fixtures.build_pdfs import build_all, marker_for_size, pdf_path_for_size
from evals.stress.metrics import (
    AttachmentRecallMetric,
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
    SessionSnapshot,
)
from evals.stress.scenarios import StressScenario, all_facts_for_scenario, get_scenarios

DEFAULT_LATENCY_BUDGET_MS = 4000
DEFAULT_COST_BUDGET_USD = 0.05
DEFAULT_BILLING_MODEL = "gpt-4o-mini"

OBSERVATION_FIELDS = [
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "cache_hit_kind",
    "last_resolved_tier",
]

RUN_META_FIELDS = [
    "scenario",
    "attachment_size_kb",
    "repeat",
    "memory_drift_score",
    "memory_drift_passed",
    "latency_budget_passed",
    "cost_budget_passed",
    "attachment_recall_passed",
]


def _snapshot_from_get(payload: dict[str, Any]) -> SessionSnapshot:
    metadata = ProjectMetadata.model_validate(payload["metadata"])
    obs = payload.get("last_turn_observation")
    turn_obs = TurnObservation.model_validate(obs) if obs else None
    return SessionSnapshot(
        summary=payload.get("summary"),
        anchors_text=payload.get("anchors_text") or "",
        metadata=metadata,
        last_turn_observation=turn_obs,
    )


def _evaluate_turn(
    *,
    snapshot: SessionSnapshot,
    observation: TurnObservation,
    result: EstimationResult | None,
    facts_prior: list[tuple[int, str]],
    current_turn: int,
    attachment_size_kb: int,
) -> dict[str, Any]:
    latency_metric = LatencyBudgetMetric(DEFAULT_LATENCY_BUDGET_MS)
    cost_metric = CostBudgetMetric(DEFAULT_COST_BUDGET_USD)

    latency_passed = latency_metric.evaluate(observation).passed
    cost_passed = cost_metric.evaluate(observation).passed

    drift_scores: list[float] = []
    for fact_turn, fact in facts_prior:
        if fact_turn >= current_turn:
            continue
        drift = MemoryDriftMetric(fact).evaluate(snapshot)
        drift_scores.append(drift.score)

    memory_score = statistics.mean(drift_scores) if drift_scores else 1.0
    memory_passed = memory_score == 1.0

    attachment_passed: bool | None = None
    if attachment_size_kb > 0 and current_turn == 1 and result is not None:
        attachment_passed = AttachmentRecallMetric(
            marker_for_size(attachment_size_kb)
        ).evaluate(result).passed

    return {
        "memory_drift_score": round(memory_score, 4),
        "memory_drift_passed": memory_passed,
        "latency_budget_passed": latency_passed,
        "cost_budget_passed": cost_passed,
        "attachment_recall_passed": attachment_passed,
    }


def _post_estimate(
    client: httpx.Client,
    *,
    sid: str,
    data: dict[str, str],
    files: dict | None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if files:
                response = client.post(f"/sessions/{sid}/estimate", data=data, files=files)
            else:
                response = client.post(f"/sessions/{sid}/estimate", data=data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code == 502 and attempt < max_attempts:
                continue
            raise
    assert last_exc is not None
    raise last_exc


def _run_iteration_http(
    *,
    client: httpx.Client,
    scenario: StressScenario,
    attachment_size_kb: int,
    repeat: int,
    max_turns: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sid = client.post("/sessions").json()["session_id"]
    facts = all_facts_for_scenario(scenario)
    turns = scenario.turns[:max_turns]

    for turn in turns:
        data = {
            "transcript": turn.transcript,
            "project_type": "web_saas",
            "detail_level": "medium",
            "output_format": "phases_table",
        }
        files = None
        if turn.turn_index == 1 and attachment_size_kb > 0:
            pdf_path = pdf_path_for_size(attachment_size_kb)
            files = {
                "attachments": (
                    pdf_path.name,
                    pdf_path.read_bytes(),
                    "application/pdf",
                )
            }

        try:
            if files:
                payload = _post_estimate(client, sid=sid, data=data, files=files)
            else:
                payload = _post_estimate(client, sid=sid, data=data, files=None)
            result = EstimationResult.model_validate(payload["result"])

            snap_payload = client.get(f"/sessions/{sid}").json()
            snapshot = _snapshot_from_get(snap_payload)
            observation = snapshot.last_turn_observation
            if observation is None:
                raise RuntimeError(f"Missing last_turn_observation for session {sid}")

            metrics = _evaluate_turn(
                snapshot=snapshot,
                observation=observation,
                result=result,
                facts_prior=facts,
                current_turn=turn.turn_index,
                attachment_size_kb=attachment_size_kb,
            )

            rows.append(
                {
                    **observation.model_dump(),
                    "scenario": scenario.name,
                    "attachment_size_kb": attachment_size_kb,
                    "repeat": repeat,
                    **metrics,
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"  turn {turn.turn_index} failed: {type(exc).__name__}: {str(exc)[:120]}",
                flush=True,
            )
            continue
    return rows


def _run_iteration_in_process(
    *,
    client: TestClient,
    scenario: StressScenario,
    attachment_size_kb: int,
    repeat: int,
    max_turns: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sid = client.post("/sessions").json()["session_id"]
    facts = all_facts_for_scenario(scenario)
    turns = scenario.turns[:max_turns]

    for turn in turns:
        data = {
            "transcript": turn.transcript,
            "project_type": "web_saas",
            "detail_level": "medium",
            "output_format": "phases_table",
        }
        files = None
        if turn.turn_index == 1 and attachment_size_kb > 0:
            pdf_path = pdf_path_for_size(attachment_size_kb)
            files = [("attachments", (pdf_path.name, pdf_path.read_bytes(), "application/pdf"))]

        if files:
            response = client.post(f"/sessions/{sid}/estimate", data=data, files=files)
        else:
            response = client.post(f"/sessions/{sid}/estimate", data=data)
        response.raise_for_status()
        payload = response.json()
        result = EstimationResult.model_validate(payload["result"])

        snap_payload = client.get(f"/sessions/{sid}").json()
        snapshot = _snapshot_from_get(snap_payload)
        observation = snapshot.last_turn_observation
        if observation is None:
            raise RuntimeError(f"Missing last_turn_observation for session {sid}")

        metrics = _evaluate_turn(
            snapshot=snapshot,
            observation=observation,
            result=result,
            facts_prior=facts,
            current_turn=turn.turn_index,
            attachment_size_kb=attachment_size_kb,
        )
        rows.append(
            {
                **observation.model_dump(),
                "scenario": scenario.name,
                "attachment_size_kb": attachment_size_kb,
                "repeat": repeat,
                **metrics,
            }
        )
    return rows


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100) * (len(ordered) - 1)))
    return ordered[idx]


def backfill_row_costs(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``cost_usd`` from tokens when a row was priced against an unknown model id."""
    if float(row.get("cost_usd") or 0) > 0:
        return row
    tokens_in = int(float(row.get("tokens_in") or 0))
    tokens_out = int(float(row.get("tokens_out") or 0))
    if not tokens_in and not tokens_out:
        return row
    updated = dict(row)
    cost_usd = _estimate_cost(DEFAULT_BILLING_MODEL, tokens_in, tokens_out)
    updated["cost_usd"] = cost_usd
    updated["cost_budget_passed"] = cost_usd <= DEFAULT_COST_BUDGET_USD
    return updated


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [backfill_row_costs(dict(row)) for row in csv.DictReader(handle)]


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = OBSERVATION_FIELDS + RUN_META_FIELDS
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def write_report(rows: list[dict[str, Any]], path: Path) -> None:
    """Generate REPORT.md from collected CSV rows."""
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["attachment_size_kb"])].append(row)

    lines: list[str] = [
        "# CAG Stress Baseline Report",
        "",
        "Baseline cuantitativo del sistema CAG conversacional previo a RAG.",
        "",
        "## Tabla resumen",
        "",
        "| scenario | attachment_kb | P50 latency_ms | P95 latency_ms | total cost USD | "
        "exact cache hit | semantic cache hit | mean memory drift |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for (scenario, size_kb), group_rows in sorted(grouped.items()):
        latencies = [float(r["latency_ms"]) for r in group_rows]
        total_cost = sum(float(r["cost_usd"]) for r in group_rows)
        exact_hits = sum(1 for r in group_rows if r.get("cache_hit_kind") == "exact")
        semantic_hits = sum(1 for r in group_rows if r.get("cache_hit_kind") == "semantic")
        drift_mean = statistics.mean(float(r["memory_drift_score"]) for r in group_rows)
        lines.append(
            f"| {scenario} | {size_kb} | {_percentile(latencies, 50):.0f} | "
            f"{_percentile(latencies, 95):.0f} | {total_cost:.4f} | "
            f"{exact_hits / len(group_rows):.2%} | {semantic_hits / len(group_rows):.2%} | "
            f"{drift_mean:.2%} |"
        )

    lines.extend(["", "## Curva 1 — latency_ms vs tokens_in", ""])
    by_scenario_tokens: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        by_scenario_tokens[row["scenario"]].append(
            (int(row["tokens_in"]), float(row["latency_ms"]))
        )
    for scenario, points in sorted(by_scenario_tokens.items()):
        lines.append(f"### {scenario}")
        lines.append("")
        lines.append("| tokens_in | latency_ms |")
        lines.append("|---:|---:|")
        for tokens_in, latency in sorted(points, key=lambda p: p[0])[:30]:
            lines.append(f"| {tokens_in} | {latency:.0f} |")
        if len(points) > 30:
            lines.append(f"| … | ({len(points) - 30} more rows in CSV) |")
        lines.append("")

    lines.extend(["## Curva 2 — coste acumulado vs turn_index", ""])
    for scenario in sorted({row["scenario"] for row in rows}):
        scenario_rows = [r for r in rows if r["scenario"] == scenario and r["attachment_size_kb"] == 0]
        if not scenario_rows:
            scenario_rows = [r for r in rows if r["scenario"] == scenario]
        by_turn: dict[int, float] = defaultdict(float)
        for row in scenario_rows:
            by_turn[int(row["turn_index"])] += float(row["cost_usd"])
        cumulative = 0.0
        lines.append(f"### {scenario}")
        lines.append("")
        lines.append("| turn_index | cost_usd (turn) | cost_usd (accum) |")
        lines.append("|---:|---:|---:|")
        for turn_index in sorted(by_turn):
            turn_cost = by_turn[turn_index] / max(
                1, sum(1 for r in scenario_rows if int(r["turn_index"]) == turn_index)
            )
            cumulative += turn_cost
            lines.append(f"| {turn_index} | {turn_cost:.6f} | {cumulative:.6f} |")
        lines.append("")

    lines.extend(["## Curva 3 — MemoryDrift recall vs N", ""])
    for scenario in sorted({row["scenario"] for row in rows}):
        scenario_rows = [r for r in rows if r["scenario"] == scenario]
        by_turn_drift: dict[int, list[float]] = defaultdict(list)
        for row in scenario_rows:
            by_turn_drift[int(row["turn_index"])].append(float(row["memory_drift_score"]))
        lines.append(f"### {scenario}")
        lines.append("")
        lines.append("| turn_index | mean memory_drift |")
        lines.append("|---:|---:|")
        for turn_index in sorted(by_turn_drift):
            mean_drift = statistics.mean(by_turn_drift[turn_index])
            lines.append(f"| {turn_index} | {mean_drift:.2%} |")
        lines.append("")

    # Quantitative reading paragraphs from data
    all_by_turn: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        all_by_turn[int(row["turn_index"])].append(float(row["memory_drift_score"]))
    break_turn = None
    for turn_index in sorted(all_by_turn):
        if statistics.mean(all_by_turn[turn_index]) < 0.6:
            break_turn = turn_index
            break

    turn1_cost = statistics.mean(
        float(r["cost_usd"]) for r in rows if int(r["turn_index"]) == 1
    )
    max_turn_index = max(int(r["turn_index"]) for r in rows)
    late_turn_rows = [r for r in rows if int(r["turn_index"]) == max_turn_index]
    late_turn_cost = (
        statistics.mean(float(r["cost_usd"]) for r in late_turn_rows) if late_turn_rows else 0.0
    )
    cost_multiplier = (late_turn_cost / turn1_cost) if turn1_cost > 0 and late_turn_cost else 0.0

    latency_p95 = _percentile([float(r["latency_ms"]) for r in rows], 95)
    attachment_rows = [r for r in rows if int(r["attachment_size_kb"]) >= 50]
    attachment_latency_p95 = (
        _percentile([float(r["latency_ms"]) for r in attachment_rows], 95)
        if attachment_rows
        else 0.0
    )

    lines.extend(["## Lectura", ""])
    if break_turn:
        lines.append(
            f"A partir del turno N={break_turn}, el recall medio del fact-tracker cae por debajo "
            f"del 60% en este baseline. Eso indica que la ventana deslizante + summary empiezan a "
            f"perder hechos tempranos antes de que falle el esquema — degradación silenciosa."
        )
    else:
        lines.append(
            "En este run el recall del fact-tracker se mantuvo ≥60% en todos los turnos medidos; "
            "la pérdida de memoria no fue el primer síntoma visible del límite del CAG."
        )
    lines.append("")
    dominant = "memoria"
    if cost_multiplier >= 2:
        dominant = "coste"
    if attachment_latency_p95 > DEFAULT_LATENCY_BUDGET_MS:
        dominant = "latencia"
    lines.append(
        f"La dimensión que más domina la degradación aquí es **{dominant}**: el turno "
        f"{max_turn_index} multiplica el coste del turno 1 por {cost_multiplier:.1f}x "
        f"(presupuesto {DEFAULT_COST_BUDGET_USD:.2f} USD/turno; coste medio turno 1 "
        f"{turn1_cost:.4f} USD, turno {max_turn_index} {late_turn_cost:.4f} USD), "
        f"y la latencia P95 global es {latency_p95:.0f} ms (SLA {DEFAULT_LATENCY_BUDGET_MS} ms). "
        f"Con adjuntos ≥50 KB la P95 sube a {attachment_latency_p95:.0f} ms. "
        f"Un caso límite que justificaría RAG sería combinar turno ≥{break_turn or max_turn_index}, "
        f"adjuntos grandes y drift de metadata — el contexto deja de caber en el presupuesto "
        f"sin que el CI lo detecte."
    )
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default="growing,pivot,contradiction",
        help="Comma-separated scenario names",
    )
    parser.add_argument(
        "--attachment-sizes",
        default="0,5,20,50,100",
        help="Comma-separated attachment sizes in KB (0 = none)",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--http", default=None, help="Base URL for live HTTP mode")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/stress/results.csv"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("evals/stress/REPORT.md"),
    )
    parser.add_argument(
        "--from-csv",
        type=Path,
        default=None,
        help="Skip the run; backfill costs and regenerate REPORT from an existing CSV",
    )
    args = parser.parse_args()

    if args.from_csv:
        rows = load_csv_rows(args.from_csv)
        write_csv(rows, args.output)
        write_report(rows, args.report)
        print(f"Backfilled costs in {args.output} ({len(rows)} rows)")
        print(f"Report written to {args.report}")
        return 0

    scenario_names = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    attachment_sizes = [int(s.strip()) for s in args.attachment_sizes.split(",") if s.strip()]
    scenarios = get_scenarios(scenario_names)

    if any(size > 0 for size in attachment_sizes):
        build_all()

    all_rows: list[dict[str, Any]] = []

    if args.http:
        with httpx.Client(base_url=args.http, timeout=300.0) as client:
            for scenario in scenarios:
                for size_kb in attachment_sizes:
                    for repeat in range(args.repeats):
                        label = f"{scenario.name} size={size_kb}KB repeat={repeat + 1}/{args.repeats}"
                        print(f"Running {label}", flush=True)
                        try:
                            all_rows.extend(
                                _run_iteration_http(
                                    client=client,
                                    scenario=scenario,
                                    attachment_size_kb=size_kb,
                                    repeat=repeat,
                                    max_turns=args.max_turns,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"ERROR {label}: {type(exc).__name__}: {str(exc)[:200]}")
                            continue
    else:
        from tests.conftest import FakeLLMWrapper

        eval_store = SessionStore(max_turns=6)
        fake = FakeLLMWrapper()
        for _ in range(args.max_turns * len(scenarios) * len(attachment_sizes) * args.repeats):
            fake.add_turn(
                metadata=ProjectMetadata(
                    project_name="Nimbus",
                    mentioned_technologies=["React", "Flutter"],
                    agreed_scope="budget locked at 30000 EUR",
                )
            )

        from app.services.estimation import EstimationService

        service = EstimationService(
            llm_wrapper=fake,
            exact_cache=None,
            semantic_cache=None,
            openai_client=None,
        )
        app.dependency_overrides[get_estimation_service] = lambda: service
        app.dependency_overrides[get_session_store] = lambda: eval_store
        try:
            with TestClient(app) as client:
                for scenario in scenarios:
                    for size_kb in attachment_sizes:
                        for repeat in range(args.repeats):
                            print(
                                f"Running {scenario.name} size={size_kb}KB repeat={repeat + 1}/{args.repeats}"
                            )
                            all_rows.extend(
                                _run_iteration_in_process(
                                    client=client,
                                    scenario=scenario,
                                    attachment_size_kb=size_kb,
                                    repeat=repeat,
                                    max_turns=args.max_turns,
                                )
                            )
        finally:
            app.dependency_overrides.pop(get_estimation_service, None)
            app.dependency_overrides.pop(get_session_store, None)

    if not all_rows:
        print("No rows collected — aborting without writing output.")
        return 1

    all_rows = [backfill_row_costs(row) for row in all_rows]
    write_csv(all_rows, args.output)
    write_report(all_rows, args.report)
    print(f"\nWrote {len(all_rows)} rows to {args.output}")
    print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
