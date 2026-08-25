from __future__ import annotations

import json
from pathlib import Path

from .agent import BusinessCodeQueryAgent


def run_validation(db, cases_path: str | Path) -> dict:
    """Run a repeatable query fixture and score evidence-backed expectations.

    This reports fixture conformance, not production-business accuracy.  A real
    accuracy claim still requires questions and authoritative material from the
    target organization.
    """
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    agent = BusinessCodeQueryAgent(db)
    results = []
    for case in cases:
        actual = agent.run(case["question"])
        answer_text = json.dumps(actual["answer"], ensure_ascii=False)
        evidence_text = json.dumps(actual["evidence"], ensure_ascii=False)
        unknown_text = " ".join(actual["answer"]["unknowns"])
        searchable = answer_text + " " + evidence_text
        checks = {
            "intent": actual["intent"] == case["expectedIntent"],
            "status": actual["evidenceStatus"] == case["expectedStatus"],
            "sources": set(case.get("requiredSources", [])) <= set(actual["metrics"]["sourceCoverage"]),
            "evidence": all(token in searchable for token in case.get("expected_evidence", [])),
            "unknowns": all(token in unknown_text for token in case.get("expected_unknowns", [])),
            "negativeClaims": all(token not in answer_text for token in case.get("must_not_claim", [])),
        }
        results.append({
            "id": case["id"], "passed": all(checks.values()), "checks": checks,
            "actualIntent": actual["intent"], "actualStatus": actual["evidenceStatus"],
            "iterations": actual["iterations"], "runId": actual["runId"],
        })
    passed = sum(1 for item in results if item["passed"])
    return {
        "fixtureType": "REPEATABLE_REPRESENTATIVE_NOT_PRODUCTION",
        "passed": passed == len(results), "passedCases": passed,
        "totalCases": len(results), "results": results,
    }
