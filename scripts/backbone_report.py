"""Generate paired reports; model reviews are provisional, never ground truth.

The implementation lives in ``business_code_agent.evaluation``; this file keeps
the historical CLI entry (``python3 scripts/backbone_report.py <dir> --reviews …``)
and re-exports the functions older frozen harness copies import.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from business_code_agent.evaluation.report import generate_report, reviewed
from business_code_agent.evaluation.scoring import parse_review, quote_matches

__all__ = ["generate_report", "parse_review", "quote_matches", "reviewed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reviews", type=Path, help="人工复核 JSON，格式参见 review-template.json")
    args = parser.parse_args()
    generate_report(args.output, args.reviews)
    print(args.output / "report.md")
