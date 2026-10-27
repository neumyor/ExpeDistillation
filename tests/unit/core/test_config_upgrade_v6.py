"""The v5 -> v6 upgrade: three learn slots become one review slot."""

from expedistill.core.config import upgrade_v5_config


def _v5():
    return {
        "benchmark": "tb", "mode": "split",
        "models": {"executor_strong": "s", "executor_weak": "w"},
        "n_attempts_strong": 1, "n_attempts_weak": 3,
        "sequence": ["baseline", "learn", "test"],
        "phases": {
            "baseline": {"kind": "execute", "role": "baseline"},
            "learn": {
                "kind": "learn", "role": "learn",
                "evidence": {"name": "segment", "K": 4,
                             "retry_limit": 3, "write_lessons": False},
                "attribution": {"name": "skill_attempts",
                                "before_steps": 4, "after_steps": 5},
                "decision": {"name": "skill_level",
                             "unread_in_won_group": "keep"},
                "author": {"name": "distill_diagnose"},
                "curate": {"name": "none", "token_budget": 100},
            },
            "test": {"kind": "execute", "role": "test"},
        },
        "schema_version": 5,
    }


def test_merges_the_three_learn_slots():
    out = upgrade_v5_config(_v5())
    assert out["schema_version"] == 6
    learn = out["phases"]["learn"]
    assert "evidence" not in learn and "attribution" not in learn
    assert "decision" not in learn
    assert learn["review"] == {
        "name": "review", "decision": "skill_level",
        "unread_in_won_group": "keep",
        "before_steps": 4, "after_steps": 5,
        "K": 4, "retry_limit": 3, "write_lessons": False,
    }
    assert learn["author"] == {"name": "distill_diagnose"}
    assert learn["curate"]["token_budget"] == 100


def test_defaults_match_the_old_absent_slots():
    raw = _v5()
    learn = raw["phases"]["learn"]
    del learn["attribution"]
    del learn["decision"]
    del learn["evidence"]["write_lessons"]
    review = upgrade_v5_config(raw)["phases"]["learn"]["review"]
    assert review["decision"] == "task_level"
    assert review["unread_in_won_group"] == "delete"
    assert review["before_steps"] == 10
    assert review["after_steps"] == 10
    assert review["write_lessons"] is True
