"""One browser pass over the fictional suite. The three-run bar is recorded in the journey."""

from __future__ import annotations

from pocket_agent.forms.evaluate import run_form_eval


def test_one_clean_pass_meets_the_bar() -> None:
    report = run_form_eval(runs=1, perturbations=False, model="none")
    assert report["bar"]["pass"]
    held = report["held_out"]
    assert held["wrong_fills"] == 0
    assert held["submits"] == 0
    assert held["crashes"] == 0
    assert held["fill_accuracy"] >= 0.95
    assert held["ask_rate"] == 1.0
