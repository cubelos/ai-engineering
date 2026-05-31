"""Multi-turn stress scenarios for CAG baseline measurement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StressTurn:
    turn_index: int
    transcript: str
    fact_to_remember: str | None = None


@dataclass(frozen=True)
class StressScenario:
    name: str
    turns: list[StressTurn]


def _growing_turns() -> list[StressTurn]:
    base = "We want a B2B CRM called Nimbus built with React and Postgres for the sales team."
    additions = [
        ("Add role-based authentication with SSO via Google Workspace.", "SSO"),
        ("We need multi-tenant isolation — each customer gets their own schema.", "multi-tenant"),
        ("Include a full audit log for every record change.", "audit log"),
        ("Add CSV export for opportunities and pipeline reports.", "CSV export"),
        ("Integrate email tracking for outbound sequences.", "email tracking"),
        ("Build a dashboard with weekly KPI widgets.", "KPI dashboard"),
        ("Add webhook notifications for deal stage changes.", "webhooks"),
        ("Support custom fields on accounts and contacts.", "custom fields"),
        ("Implement bulk import from Salesforce.", "Salesforce import"),
        ("Add mobile-responsive views for field reps.", "mobile-responsive"),
        ("Include SLA timers on support tickets linked to deals.", "SLA timers"),
        ("Add territory management for regional sales leads.", "territory management"),
        ("Build an admin console for tenant configuration.", "admin console"),
        ("Add two-factor authentication for all users.", "two-factor authentication"),
        ("Include data retention policies configurable per tenant.", "data retention"),
        ("Add API rate limiting for public integrations.", "rate limiting"),
        ("Support sandbox environments for customer testing.", "sandbox environments"),
        ("Add scheduled report delivery via email.", "scheduled reports"),
        ("Include GDPR data export and deletion tooling.", "GDPR"),
    ]
    turns = [
        StressTurn(1, base, "project name: Nimbus"),
        StressTurn(2, additions[0][0], "stack includes React"),
    ]
    for idx, (text, fact) in enumerate(additions[1:], start=3):
        turns.append(StressTurn(idx, text, fact))
    return turns


def _pivot_turns() -> list[StressTurn]:
    turns = [
        StressTurn(
            1,
            "We are scoping a field-sales mobile app called FieldPulse with React Native and a Node API.",
            "project name: FieldPulse",
        ),
        StressTurn(
            2,
            "The React Native app needs offline-first sync for visit notes.",
            "stack includes React",
        ),
        StressTurn(
            3,
            "Use Postgres for the Node API with row-level security.",
            "Postgres",
        ),
        StressTurn(
            4,
            "Push notifications via Firebase for visit reminders.",
            "Firebase",
        ),
        StressTurn(
            5,
            "Important pivot: we are dropping React Native and rebuilding the client in Flutter for iOS and Android.",
            "stack includes Flutter",
        ),
    ]
    flutter_features = [
        "Build the Flutter app with bloc state management.",
        "Add biometric login in the Flutter client.",
        "Implement background geolocation tracking in Flutter.",
        "Add photo capture and upload from the Flutter app.",
        "Support tablet layouts in the Flutter UI.",
        "Add in-app messaging between reps and managers.",
        "Integrate Stripe billing in the Flutter checkout flow.",
        "Add dark mode theming to the Flutter app.",
        "Build offline queue replay in Flutter.",
        "Add analytics events from the Flutter client.",
        "Implement deep links into Flutter screens.",
        "Add push notification handling in Flutter.",
        "Support multiple languages in the Flutter UI.",
        "Add a manager approval workflow in Flutter.",
        "Include crash reporting for the Flutter build.",
    ]
    for offset, text in enumerate(flutter_features, start=6):
        turns.append(StressTurn(offset, text, "stack includes Flutter"))
    return turns


def _contradiction_turns() -> list[StressTurn]:
    turns = [
        StressTurn(
            1,
            "We need an internal procurement portal called SpendWise for purchase requests.",
            "project name: SpendWise",
        ),
        StressTurn(
            2,
            "Approvers must receive email notifications for pending requests.",
            "email notifications",
        ),
        StressTurn(
            3,
            "The budget is locked at 30000 EUR for the entire first release.",
            "budget locked: 30000 EUR",
        ),
        StressTurn(
            4,
            "Add multi-level approval chains based on department.",
            "approval chains",
        ),
        StressTurn(
            5,
            "Integrate with our existing LDAP directory for user lookup.",
            "LDAP",
        ),
        StressTurn(
            6,
            "Include PDF export of approved purchase orders.",
            "PDF export",
        ),
        StressTurn(
            7,
            "Add vendor catalog search with full-text indexing.",
            "vendor catalog",
        ),
        StressTurn(
            8,
            "Update: the budget is now 80000 EUR because legal requires a compliance module.",
            "budget: 80000 EUR",
        ),
    ]
    neutral = [
        "Add a compliance checklist module for regulated purchases.",
        "Build reporting dashboards for finance managers.",
        "Support attachment uploads on each request.",
        "Add SLA tracking for approver response times.",
        "Include audit trails for every status change.",
        "Integrate Slack notifications for urgent approvals.",
        "Add bulk import of vendor price lists.",
        "Support delegated approvers during vacations.",
        "Build a mobile-friendly approval view.",
        "Add SSO via Okta for enterprise users.",
        "Include spend analytics by cost center.",
        "Add configurable approval thresholds.",
    ]
    for offset, text in enumerate(neutral, start=9):
        turns.append(StressTurn(offset, text, None))
    return turns


_SCENARIOS: dict[str, StressScenario] = {
    "growing": StressScenario(name="growing", turns=_growing_turns()),
    "pivot": StressScenario(name="pivot", turns=_pivot_turns()),
    "contradiction": StressScenario(name="contradiction", turns=_contradiction_turns()),
}


def get_scenarios(names: list[str] | None = None) -> list[StressScenario]:
    if not names:
        return list(_SCENARIOS.values())
    missing = [n for n in names if n not in _SCENARIOS]
    if missing:
        raise ValueError(f"Unknown scenarios: {missing}. Available: {sorted(_SCENARIOS)}")
    return [_SCENARIOS[n] for n in names]


def all_facts_for_scenario(scenario: StressScenario) -> list[tuple[int, str]]:
    """Return (turn_index, fact) pairs declared by the scenario."""
    return [
        (turn.turn_index, turn.fact_to_remember)
        for turn in scenario.turns
        if turn.fact_to_remember
    ]
