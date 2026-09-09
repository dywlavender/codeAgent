"""Paired knowledge-backbone evaluation shared by the CLI and the workbench.

The command line (``scripts/evaluate_backbone.py``) and the effect-verification
page call the same orchestration, so both produce the same run records, review
files and summaries.
"""

from .report import build_view_report, generate_report, reviewed
from .runner import (ARM_DESCRIPTIONS_AB, ARM_DESCRIPTIONS_ABCDE, ARMS_AB, ARMS_ABCDE,
                     baseline_documents, freeze_batch, load_protocol, plan_jobs, rejudge_output,
                     resolve_project_sources, run_batch, summarize_pairs, validate_suite)
from .scoring import human_review_score, model_review_score, parse_review, quote_matches

__all__ = [
    "ARM_DESCRIPTIONS_AB",
    "ARM_DESCRIPTIONS_ABCDE",
    "ARMS_AB",
    "ARMS_ABCDE",
    "baseline_documents",
    "build_view_report",
    "freeze_batch",
    "generate_report",
    "human_review_score",
    "load_protocol",
    "model_review_score",
    "parse_review",
    "plan_jobs",
    "quote_matches",
    "rejudge_output",
    "resolve_project_sources",
    "reviewed",
    "run_batch",
    "summarize_pairs",
    "validate_suite",
]
