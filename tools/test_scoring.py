#!/usr/bin/env python3
"""Lightweight tests for scoring boundaries."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import contains_any, score_text


def test_quant_not_quantum():
    assert contains_any("quant trading strategy", ["quant"])
    assert not contains_any("munich quantum toolkit", ["quant"])
    assert not contains_any("llm quantization awq", ["quant"])


def test_hard_negative():
    r = score_text(
        "munich-quantum-toolkit/qecc quantum error correction",
        finance_signals=["quant", "finance"],
        topic_signals=["quant", "backtest"],
        skill_signals=["toolkit"],
        blacklist=[],
    )
    assert r["rejected"]


def test_good_finance_topic():
    r = score_text(
        "awesome credit risk rating model for bond default analysis",
        finance_signals=["finance", "bond", "credit"],
        topic_signals=["credit risk", "rating", "default"],
        skill_signals=["model", "analysis"],
        blacklist=["game"],
    )
    assert not r["rejected"]
    assert r["finance_hits"]
    assert r["topic_hits"]
    assert r["score"] >= 6


if __name__ == "__main__":
    test_quant_not_quantum()
    test_hard_negative()
    test_good_finance_topic()
    print("OK")
