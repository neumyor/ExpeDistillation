"""Unit tests for the D11 retrieval blocks (pure functions, no model/IO).

The tests pin the frozen retrieval semantics: determinism (same input ->
bit-identical output; input list order never matters), the average-link
merge with its pinned threshold and lexicographic tie-breaks, the two-step
routing (category truncation really excludes skills from the pool), the
top-1-only min_score gate, and the retrieval-document weighting
(applicability section bodies x3, everything else x1) -- the unmarked
case is verified against a hand-computed TF-IDF cosine. Fixtures
reproduce the production bank shapes (decision 10): 29 groups / 42
skills sized [5, 3, 3, 2, 2, 2, 2, 2, 1 x 21] with zero ungrouped
skills, plus the 62-group / 97-skill pressure profile.
"""

import math

import pytest

from expedistill.blocks.retrieve import (
    CATEGORY_MERGE_THRESHOLD,
    derive_categories,
    select_skills,
)
from expedistill.core.models import Skill


def make_skill(skill_id, content, group=None):
    return Skill(skill_id=skill_id, content=content,
                 subtask_id="traj/tb:fixture/00/seg00",
                 source_subtasks=["traj/tb:fixture/00/seg00"], group=group)


def bank_29():
    """The production 42-skill bank's real shape (decision 10): 29 groups
    sized [5, 3, 3, 2, 2, 2, 2, 2, 1 x 21], zero ungrouped skills.
    Vocabularies are disjoint per group except one twin pair with
    identical texts (profile cosine 1.0 >= threshold: the one guaranteed
    merge)."""
    skills = []
    for index, size in enumerate([5, 3, 3, 2, 2, 2, 2, 2]):
        for member in range(size):
            skills.append(make_skill(
                f"traj/tb:t{index:02d}/strong/00/seg{member:02d}/skill",
                f"topic{index:02d} step{index:02d}m{member:02d}",
                group=f"group {index:02d}"))
    for number in range(8, 27):
        skills.append(make_skill(
            f"traj/tb:s{number:02d}/strong/00/seg00/skill",
            f"topic{number:02d} solo{number:02d}",
            group=f"group {number:02d}"))
    for tag in ("alpha", "beta"):
        skills.append(make_skill(
            f"traj/tb:tw{tag}/strong/00/seg00/skill",
            "twin vocabulary shared", group=f"twin {tag}"))
    assert len(skills) == 42
    return skills


def bank_62():
    """The unfiltered v3 bank's pressure profile: 62 groups / 97 skills
    sized [5, 5, 4, 4, 3 x 6, 2 x 9, 1 x 43]. The two size-5 groups are
    twins (guaranteed merge); everything else is disjoint."""
    sizes = [5, 5, 4, 4] + [3] * 6 + [2] * 9 + [1] * 43
    skills = []
    for index, size in enumerate(sizes):
        for member in range(size):
            content = (f"twinbank gear{member}" if index < 2 else
                       f"bank{index:02d} piece{index:02d}m{member:02d}")
            skills.append(make_skill(
                f"traj/tb:p{index:02d}/strong/00/seg{member:02d}/skill",
                content, group=f"press {index:02d}"))
    assert len(skills) == 97
    return skills


# ---------------------------------------------------------------------------
# derive_categories
# ---------------------------------------------------------------------------

def test_merge_threshold_is_the_pinned_constant():
    # Decision 10 pins the agglomeration threshold; it is a constant, not
    # a config knob.
    assert CATEGORY_MERGE_THRESHOLD == 0.25


def test_derive_categories_empty_bank():
    assert derive_categories([]) == {}


def test_derive_categories_ungrouped_skill_is_own_category():
    bank = [
        make_skill("s/grouped", "log rotation syslog", group="log hygiene"),
        make_skill("s/lonely", "audio spectrogram wav"),
    ]
    # An ungrouped skill is its own category under its own id and never
    # merges with anything (contract 15).
    assert derive_categories(bank) == {
        "log hygiene": "log hygiene", "s/lonely": "s/lonely"}


def test_derive_categories_merges_only_similar_groups():
    bank = [
        make_skill("s/a1", "shared vocabulary text", group="gadget fixing"),
        make_skill("s/a2", "shared vocabulary text",
                   group="gadget fixing pro"),
        make_skill("s/z1", "unrelated zeta topic", group="zeta work"),
    ]
    # Identical profiles merge (cosine 1.0 >= threshold); the category is
    # named by the lexicographically smallest member. Disjoint
    # vocabularies stay unmerged.
    assert derive_categories(bank) == {
        "gadget fixing": "gadget fixing",
        "gadget fixing pro": "gadget fixing",
        "zeta work": "zeta work",
    }


def test_derive_categories_29_group_real_shape():
    bank = bank_29()
    result = derive_categories(bank)
    assert len(result) == 29
    assert set(result) == {skill.group for skill in bank}
    assert set(result.values()) <= set(result)
    # The twin singleton pair is the only merge: 28 categories.
    assert result["twin alpha"] == result["twin beta"] == "twin alpha"
    assert len(set(result.values())) == 28


def test_derive_categories_62_group_pressure_profile():
    bank = bank_62()
    result = derive_categories(bank)
    assert len(result) == 62
    assert len(set(result.values())) == 61  # only the twin pair merges
    assert result["press 00"] == result["press 01"] == "press 00"


def test_derive_categories_deterministic_bit_for_bit():
    bank = bank_29() + bank_62()
    first = derive_categories(bank)
    assert derive_categories(bank) == first
    assert list(derive_categories(bank)) == list(first)
    # Input list order never matters.
    assert derive_categories(list(reversed(bank))) == first


def test_derive_categories_rejects_malformed_records():
    with pytest.raises(ValueError):
        derive_categories([{"content": "missing id"}])
    with pytest.raises(ValueError):
        derive_categories([{"skill_id": "s/x"}])


def test_records_accepted_as_objects_or_mappings():
    objects = [
        make_skill("s/g", "shared vocabulary text", group="alpha ops"),
        make_skill("s/h", "shared vocabulary text", group="alpha operations"),
        make_skill("s/u", "solo ungrouped text"),
    ]
    mappings = [
        {"skill_id": "s/g", "content": "shared vocabulary text",
         "group": "alpha ops"},
        {"skill_id": "s/h", "content": "shared vocabulary text",
         "group": "alpha operations"},
        {"skill_id": "s/u", "content": "solo ungrouped text"},
    ]
    categories = derive_categories(objects)
    assert derive_categories(mappings) == categories
    kwargs = dict(top_k=2, max_categories=2, min_score=0.0)
    from_objects = select_skills("shared vocabulary", objects, categories,
                                 **kwargs)
    from_mappings = select_skills("shared vocabulary", mappings, categories,
                                  **kwargs)
    assert ([s["skill_id"] for s in from_objects["scores"]] ==
            [s["skill_id"] for s in from_mappings["scores"]])
    assert from_objects["scores"] == from_mappings["scores"]


# ---------------------------------------------------------------------------
# select_skills: shape, determinism, tie-breaks, routing
# ---------------------------------------------------------------------------

def test_select_skills_result_shape_and_identity():
    bank = [
        make_skill("s/a", "parse log rotate", group="logs"),
        make_skill("s/b", "parse config file", group="logs"),
        make_skill("s/c", "audio spectrogram", group="audio"),
    ]
    categories = derive_categories(bank)
    result = select_skills("parse log", bank, categories, top_k=2,
                           max_categories=2, min_score=0.0)
    assert set(result) == {"selected", "routed", "scores", "threshold_passed"}
    assert isinstance(result["threshold_passed"], bool)
    assert all(isinstance(name, str) for name in result["routed"])
    assert all(set(entry) == {"skill_id", "score"}
               for entry in result["scores"])
    # selected carries the ORIGINAL records, not copies.
    assert all(any(record is skill for skill in bank)
               for record in result["selected"])
    # selected is exactly the head of the pool ranking.
    assert [s.skill_id for s in result["selected"]] == [
        entry["skill_id"] for entry in result["scores"][:2]]
    # scores are non-increasing.
    scores = result["scores"]
    assert all(scores[i]["score"] >= scores[i + 1]["score"]
               for i in range(len(scores) - 1))


def test_select_skills_scores_ties_break_by_skill_id():
    bank = [
        make_skill("s/z", "identical content here", group="g"),
        make_skill("s/a", "identical content here", group="g"),
        make_skill("s/m", "identical content here", group="g"),
    ]
    result = select_skills("identical content", bank, {"g": "g"},
                           top_k=3, max_categories=1, min_score=0.0)
    assert [entry["skill_id"] for entry in result["scores"]] == \
        ["s/a", "s/m", "s/z"]
    assert (result["scores"][0]["score"]
            == result["scores"][1]["score"]
            == result["scores"][2]["score"])
    assert [s.skill_id for s in result["selected"]] == ["s/a", "s/m", "s/z"]


def test_select_skills_routed_ties_break_by_category_name():
    bank = [
        make_skill("s/1", "alpha xone", group="zeta one"),
        make_skill("s/2", "alpha yone", group="beta two"),
    ]
    result = select_skills("alpha", bank, {}, top_k=2, max_categories=2,
                           min_score=0.0)
    # Symmetric docs -> equal category scores -> name order decides.
    assert result["routed"] == ["beta two", "zeta one"]
    assert [entry["skill_id"] for entry in result["scores"]] == ["s/1", "s/2"]


def test_select_skills_routing_truncation_drops_better_matching_skill():
    bank = [
        make_skill("skill/pure", "parse", group="pure"),
        make_skill("skill/diluted", "parse log", group="diluted"),
        make_skill("skill/diluter",
                   " ".join(f"noise{i}" for i in range(8)), group="diluted"),
    ]
    one = select_skills("parse log", bank, {}, top_k=5, max_categories=1,
                        min_score=0.5)
    # The diluter sinks the "diluted" category below "pure" in routing,
    # so only skill/pure enters the pool -- even though skill/diluted
    # alone would out-rank it by far.
    assert one["routed"] == ["pure"]
    assert [entry["skill_id"] for entry in one["scores"]] == ["skill/pure"]
    assert [s.skill_id for s in one["selected"]] == ["skill/pure"]
    assert one["threshold_passed"]

    both = select_skills("parse log", bank, {}, top_k=5, max_categories=2,
                         min_score=0.0)
    assert both["routed"] == ["pure", "diluted"]
    assert both["scores"][0]["skill_id"] == "skill/diluted"


def test_select_skills_ungrouped_skill_participates_in_routing():
    bank = [
        make_skill("s/grouped", "log rotation syslog", group="log hygiene"),
        make_skill("s/lonely", "audio spectrogram wav"),
    ]
    categories = derive_categories(bank)
    result = select_skills("audio spectrogram", bank, categories, top_k=1,
                           max_categories=1, min_score=0.1)
    assert result["routed"] == ["s/lonely"]
    assert [s.skill_id for s in result["selected"]] == ["s/lonely"]
    assert result["threshold_passed"]


def test_select_skills_empty_bank_selects_nothing():
    result = select_skills("anything", [], {}, top_k=3, max_categories=2,
                           min_score=0.0)
    assert result == {"selected": [], "routed": [], "scores": [],
                      "threshold_passed": False}


def test_select_skills_deterministic_and_input_order_invariant():
    bank = bank_29()
    categories = derive_categories(bank)
    kwargs = dict(top_k=5, max_categories=3, min_score=0.0)
    first = select_skills("topic00 step00m00", bank, categories, **kwargs)
    assert select_skills("topic00 step00m00", bank, categories,
                         **kwargs) == first
    shuffled = list(reversed(bank))
    other = select_skills("topic00 step00m00", shuffled, categories, **kwargs)
    assert other["routed"] == first["routed"]
    assert other["scores"] == first["scores"]
    assert other["threshold_passed"] == first["threshold_passed"]
    assert [s.skill_id for s in other["selected"]] == \
        [s.skill_id for s in first["selected"]]
    # The best match is the group-00 member carrying both task tokens.
    assert first["scores"][0]["skill_id"] == \
        "traj/tb:t00/strong/00/seg00/skill"


def test_select_skills_rejects_invalid_arguments():
    bank = [make_skill("s/a", "some text", group="g")]
    categories = {"g": "g"}
    good = dict(top_k=1, max_categories=1, min_score=0.0)
    with pytest.raises(ValueError):
        select_skills("t", bank, categories,
                      **{**good, "top_k": 0})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories, **{**good, "top_k": True})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories, **{**good, "max_categories": 0})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories, **{**good, "min_score": 1.0})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories, **{**good, "min_score": -0.1})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories, **{**good, "min_score": True})
    with pytest.raises(ValueError):
        select_skills("t", bank, categories,
                      **{**good, "min_score": float("nan")})
    with pytest.raises(ValueError):
        select_skills(None, bank, categories, **good)
    with pytest.raises(TypeError):
        select_skills("t", bank, None, **good)
    with pytest.raises(ValueError):
        select_skills("t", [{"content": "no id"}], categories, **good)
    with pytest.raises(ValueError):
        select_skills("t", [{"skill_id": "s/x"}], categories, **good)


# ---------------------------------------------------------------------------
# select_skills: the min_score gate
# ---------------------------------------------------------------------------

GATE_BANK = [
    make_skill("s/strong", "parse log extra", group="g"),
    make_skill("s/zero", "zzz", group="g"),
]


def test_select_skills_min_score_gates_top1_only():
    # Top-1 cosine is 2/sqrt(6) ~ 0.816: above 0.5, so the gate passes
    # and rank 2 (score 0.0, BELOW the threshold) is still selected --
    # the gate applies to the top-1 only (contract 11).
    result = select_skills("parse log", GATE_BANK, {"g": "g"}, top_k=2,
                           max_categories=1, min_score=0.5)
    assert result["threshold_passed"]
    assert [s.skill_id for s in result["selected"]] == ["s/strong", "s/zero"]
    assert result["scores"][1]["score"] < 0.5


def test_select_skills_below_threshold_empty_selection():
    result = select_skills("parse log", GATE_BANK, {"g": "g"}, top_k=2,
                           max_categories=1, min_score=0.9)
    assert not result["threshold_passed"]
    assert result["selected"] == []
    # The audit record stays complete: routing and the pool ranking are
    # reported even when nothing is injected.
    assert result["routed"] == ["g"]
    assert [entry["skill_id"] for entry in result["scores"]] == \
        ["s/strong", "s/zero"]


# ---------------------------------------------------------------------------
# select_skills: retrieval-document weighting
# ---------------------------------------------------------------------------

def test_select_skills_unmarked_scoring_matches_hand_computed_tf_idf():
    # Two unmarked skills: the whole text counts x1, so the score is the
    # plain smoothed TF-IDF cosine. With N=2 documents:
    # idf(df=1) = ln(3/2) + 1, idf(df=2) = ln(3/3) + 1 = 1.
    bank = [
        make_skill("s/one", "alpha beta gamma"),
        make_skill("s/two", "beta delta"),
    ]
    categories = derive_categories(bank)
    result = select_skills("alpha unseen", bank, categories, top_k=2,
                           max_categories=2, min_score=0.0)
    weight = math.log(1.5) + 1.0
    expected = weight / math.sqrt(2.0 * weight * weight + 1.0)
    # "unseen" is out of bank vocabulary and drops out (OOV rule).
    assert result["routed"] == ["s/one", "s/two"]
    assert result["scores"][0] == {"skill_id": "s/one",
                                   "score": pytest.approx(expected)}
    assert result["scores"][1] == {"skill_id": "s/two", "score": 0.0}

    # Tokenizer pins: case folding and punctuation stripping...
    folded = select_skills("  ALPHA!  ", bank, categories, top_k=2,
                           max_categories=2, min_score=0.0)
    assert folded["scores"] == result["scores"]
    # ...and "_" joining inside a token: "alpha_beta" never matches
    # "alpha" (zero overlap -> gate fails at a nonzero min_score).
    underscore = select_skills(
        "alpha", [make_skill("s/us", "alpha_beta zeta")], {"s/us": "s/us"},
        top_k=1, max_categories=1, min_score=0.5)
    assert underscore["scores"][0]["score"] == 0.0
    assert not underscore["threshold_passed"]


def test_select_skills_applicability_section_weight_decides_ranking():
    # Identical docs except which section carries "alpha": same token
    # multiset, same norms -- only the x3 applicability weighting can
    # separate them.
    bank = [
        make_skill("s/one", "[When to use] alpha [Steps] beta"),
        make_skill("s/two", "[Steps] alpha [When to use] beta"),
    ]
    categories = derive_categories(bank)
    for task, winner in (("alpha", "s/one"), ("beta", "s/two")):
        result = select_skills(task, bank, categories, top_k=1,
                               max_categories=2, min_score=0.0)
        assert result["scores"][0]["skill_id"] == winner
        assert result["scores"][0]["score"] > result["scores"][1]["score"]


def test_select_skills_marked_beats_unmarked_and_other_sections_do_not():
    bank = [
        make_skill("skill/a-plain", "tool case alpha"),
        make_skill("skill/b-marked", "tool case [When to use] alpha"),
        make_skill("skill/c-other", "tool case [Steps] alpha"),
    ]
    result = select_skills("alpha", bank, {}, top_k=3, max_categories=3,
                           min_score=0.0)
    scores = {entry["skill_id"]: entry["score"]
              for entry in result["scores"]}
    # The [When to use] boost lifts the marked doc above the plain one
    # even though the marker header adds mass against it...
    assert scores["skill/b-marked"] > scores["skill/a-plain"]
    # ...while a [Steps] section stays x1: its header only dilutes.
    assert scores["skill/a-plain"] > scores["skill/c-other"]


def test_select_skills_failure_reference_trigger_section_weighted():
    bank = [
        make_skill("s/none", "tool case alpha"),
        make_skill("s/proc", "tool case [When to use] alpha"),
        make_skill("s/fail", "tool case [Trigger] alpha"),
    ]
    result = select_skills("alpha", bank, {}, top_k=3, max_categories=3,
                           min_score=0.0)
    scores = {entry["skill_id"]: entry["score"]
              for entry in result["scores"]}
    # Both marker families' applicability sections ([When to use] for
    # procedure skills, [Trigger] for failure references) are weighted.
    assert scores["s/proc"] > scores["s/none"]
    assert scores["s/fail"] > scores["s/none"]
