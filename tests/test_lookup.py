from pathlib import Path
from types import SimpleNamespace

import pytest

from icdjev import ICDLookup

XML = Path(__file__).parent / "data" / "mini_claml.xml"


class FakeJudge:
    """Says yes to nodes whose code is in `yes`; records every question batch."""

    def __init__(self, yes):
        self.yes, self.calls, self.closed = yes, 0, False

    def judge(self, state, questions):
        self.calls += 1
        answers = {
            k: {"noul": 0.99 if state["candidates"][k]["code"] in self.yes else 0.01}
            for k in questions
        }
        return SimpleNamespace(answers=answers, model="fake", usage={})

    def close(self):
        self.closed = True


def test_descends_to_leaf_and_returns_path():
    from icdjev import Catalogue

    cat = Catalogue.load(XML)
    # find any assignable leaf by walking from chapters
    stack, target = list(cat.chapters), None
    while stack:
        n = stack.pop()
        if n.children:
            stack += cat.children(n.code)
        elif cat.validate(n.code)[0]:
            target = n
            break
    assert target is not None
    yes = {p.code for p in cat.path(target.code)}
    judge = FakeJudge(yes)
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        out = icd.get_icd("anything")
    assert [c.code for c in out] == [target.code]
    assert out[0].path[-1] == target.code
    assert judge.closed


def test_nothing_supported_returns_empty():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge(set())) as icd:
        assert icd.get_icd("nothing") == []


def test_lexical_candidate_survives_missed_ancestor():
    judge = FakeJudge({"T13"})
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        out = icd.get_icd("High blood pressure")
    assert [code.code for code in out] == ["T13"]


def test_final_verification_rejects_inferred_leaf():
    class VerificationJudge(FakeJudge):
        def judge(self, state, questions):
            response = super().judge(state, questions)
            if state.get("stage") == "verification":
                response.answers = {key: {"noul": 0.01} for key in questions}
            return response

    with ICDLookup(catalogue_path=XML, judge=VerificationJudge({"T13"})) as icd:
        assert icd.get_icd("High blood pressure") == []


class ScoredJudge(FakeJudge):
    def judge(self, state, questions):
        score = 0.65 if state["stage"] == "verification" else 0.7
        return SimpleNamespace(
            answers={
                key: {"noul": score if candidate["code"] == "T13" else 0.01}
                for key, candidate in state["candidates"].items()
            }
        )


def test_per_call_threshold_applies_to_traversal_and_verification_without_mutation():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        assert icd.get_icd("High blood pressure") == []
        result = icd.get_icd("High blood pressure", threshold=0.6)
        assert [code.code for code in result] == ["T13"]
        assert result[0].probability == 0.65
        assert icd.get_icd("High blood pressure", threshold=0.68) == []
        assert icd.threshold == 0.8
        assert icd.get_icd("High blood pressure") == []


def test_omitted_or_none_threshold_inherits_instance_default():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set()), threshold=0.6) as icd:
        assert [code.code for code in icd.get_icd("High blood pressure")] == ["T13"]
        assert [code.code for code in icd.get_icd("High blood pressure", threshold=None)] == ["T13"]
        assert icd.get_icd("High blood pressure", threshold=0.8) == []
        assert icd.threshold == 0.6


@pytest.mark.parametrize("threshold", [0, -0.1, 1.1, float("nan"), float("inf")])
def test_invalid_per_call_threshold_rejected_before_judgment(threshold):
    judge = FakeJudge(set())
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        with pytest.raises(ValueError, match="threshold"):
            icd.get_icd("High blood pressure", threshold=threshold)
    assert judge.calls == 0


def test_per_call_threshold_accepts_upper_boundary():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge({"T13"})) as icd:
        assert icd.get_icd("High blood pressure", threshold=1.0) == []


def test_review_report_preserves_accepted_matches_and_does_not_add_judge_calls():
    judge = FakeJudge({"T13"})
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        matches = icd.get_icd("High blood pressure")
        calls = judge.calls
        report = icd.get_icd("High blood pressure", include_candidates=3)
    assert report.matches == matches
    assert judge.calls == calls * 2
    assert report.threshold == 0.8
    assert report.needs_review is True
    assert report.truncated is False
    candidate = next(c for c in report.candidates if c.code == "T13")
    assert candidate.status == "accepted"
    assert candidate.assignable is True
    assert candidate.traversal_score == candidate.verification_score == 0.99
    assert candidate.support_score == matches[0].probability
    assert len({c.code for c in report.candidates}) == len(report.candidates)
    import json

    assert json.loads(json.dumps(report.to_dict()))["matches"][0]["code"] == "T13"


def test_review_report_exposes_below_threshold_candidate_without_verification():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        report = icd.get_icd("High blood pressure", include_candidates=3)
        candidate = next(c for c in report.candidates if c.code == "T13")
        assert report.matches == []
        assert candidate.status == "below_threshold"
        assert candidate.traversal_score == candidate.support_score == 0.7
        assert candidate.verification_score is None
        assert candidate.reason
        assert icd.get_icd("High blood pressure", include_candidates=0) == []


def test_review_report_exposes_verification_rejection_and_override():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        report = icd.get_icd("High blood pressure", threshold=0.68, include_candidates=3)
        candidate = next(c for c in report.candidates if c.code == "T13")
        assert report.matches == []
        assert report.threshold == 0.68
        assert candidate.status == "verification_rejected"
        assert candidate.traversal_score == 0.7
        assert candidate.verification_score == candidate.support_score == 0.65
        assert icd.threshold == 0.8
        accepted = icd.get_icd("High blood pressure", threshold=0.6, include_candidates=3)
        assert [c.code for c in accepted.matches] == ["T13"]


def test_review_report_marks_hierarchy_nodes_and_depth_limit():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge({"T1"}), max_depth=1) as icd:
        report = icd.get_icd("nothing", include_candidates=3)
        assert all(c.assignable for c in report.candidates)
        assert report.candidates == []
        assert report.matches == []
        assert report.truncated is True
        assert "T13" not in {c.code for c in report.candidates}


@pytest.mark.parametrize("flag", [True, False, "3", -1, 1.5, None])
def test_candidate_limit_requires_nonnegative_integer_before_judgment(flag):
    judge = FakeJudge(set())
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        with pytest.raises(ValueError, match="include_candidates"):
            icd.get_icd("High blood pressure", include_candidates=flag)
    assert judge.calls == 0


def test_candidate_limit_selects_highest_scores_and_excludes_hierarchy():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        report = icd.get_icd("High blood pressure", include_candidates=1)
        assert [c.code for c in report.candidates] == ["T13"]
        assert all(c.assignable for c in report.candidates)
        larger = icd.get_icd("Test fracture High blood pressure", include_candidates=3)
        assert len(larger.candidates) == 3
        assert larger.candidates == sorted(
            larger.candidates, key=lambda c: (-c.support_score, c.code)
        )


def test_best_score_returns_verified_below_threshold_match_without_mutating_default():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        report = icd.get_icd("High blood pressure", include_candidates=3, best_score=True)
        assert report.best_score is True
        assert 1 <= len(report.matches) <= 3
        match = report.matches[0]
        assert match.code == "T13"
        assert match.probability == 0.65
        assert match.below_threshold is True
        assert match.threshold_bypassed is True
        candidate = report.candidates[0]
        assert candidate.status == "selected_below_threshold"
        assert candidate.verification_score == 0.65
        assert icd.threshold == 0.8
        assert icd.get_icd("High blood pressure") == []


def test_best_score_without_candidates_returns_one_flagged_match():
    with ICDLookup(catalogue_path=XML, judge=ScoredJudge(set())) as icd:
        matches = icd.get_icd("High blood pressure", best_score=True)
        assert len(matches) == 1
        assert matches[0].code == "T13"
        assert matches[0].threshold_bypassed is True


def test_best_score_above_threshold_not_flagged_as_bypassed():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge({"T13"})) as icd:
        report = icd.get_icd("High blood pressure", include_candidates=1, best_score=True)
        assert report.matches[0].below_threshold is False
        assert report.matches[0].threshold_bypassed is False
        assert report.candidates[0].status == "accepted"


@pytest.mark.parametrize("flag", [1, None, "true"])
def test_best_score_requires_boolean_before_judgment(flag):
    judge = FakeJudge(set())
    with ICDLookup(catalogue_path=XML, judge=judge) as icd:
        with pytest.raises(ValueError, match="best_score"):
            icd.get_icd("High blood pressure", best_score=flag)
    assert judge.calls == 0


def test_best_score_ranks_by_verification_not_just_traversal():
    class RankingJudge(FakeJudge):
        def judge(self, state, questions):
            scores = (
                {"T13": 0.1, "T12.0": 0.7, "T12.1": 0.0}
                if state["stage"] == "verification"
                else {"T13": 0.99, "T12": 0.99, "T12.0": 0.8, "T12.1": 0.9}
            )
            return SimpleNamespace(
                answers={
                    key: {"noul": scores.get(candidate["code"], 0.01)}
                    for key, candidate in state["candidates"].items()
                }
            )

    with ICDLookup(catalogue_path=XML, judge=RankingJudge(set())) as icd:
        report = icd.get_icd(
            "Test fracture High blood pressure", include_candidates=3, best_score=True
        )
        assert [m.code for m in report.matches][:2] == ["T12.0", "T13"]
        assert [c.code for c in report.candidates] == [m.code for m in report.matches]
        assert report.matches[0].probability == 0.7
        assert all(m.threshold_bypassed for m in report.matches)


def test_best_score_no_evaluated_assignable_codes_returns_empty():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge(set())) as icd:
        assert icd.get_icd("nothing", best_score=True) == []


def test_rejects_empty_text():
    with ICDLookup(catalogue_path=XML, judge=FakeJudge(set())) as icd, pytest.raises(ValueError):
        icd.get_icd("  ")
