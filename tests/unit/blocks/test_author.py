"""Unit tests for the G2/G3/G6 authoring blocks (llm.fake only, no disk IO).

Wiring is asserted exactly: the user message sent to the client must equal the
named prompt template rendered with the expected variables, so a variable that
is wired to the wrong field (e.g. context_summary leaking into distill mode)
fails loudly. Protocol v2 (decision section 3.3) adds the structural
contract: write_skill guards the mode/outcome precondition, parses the G2
JSON contract, validates it with validate_skill_sections, and stores
render_skill_content's marker text; skill_markers reads that text back; and
rewrite_skill requires the rewrite to keep the original's marker set. The
2026-10-13 incident (docs/decisions/g2_multiline_and_json_extraction_
fix_20261014.md) relaxed the contract to accept multi-line values while
keeping the marker defense as a per-line check. None of these tests touch
tmp_path or run_dir: the blocks must not write anything anywhere.
"""

import pytest

from expedistill.core.ids import (
    make_exam_id,
    make_skill_id,
    make_subtask_id,
    make_task_id,
    make_trajectory_id,
)
from expedistill.core.models import (
    FAILURE_REFERENCE_MARKERS,
    PROCEDURE_MARKERS,
    Skill,
    Subtask,
)
from expedistill.infra.reliability import ModelOutputError
from expedistill.llm import json_script, make_fake_handle
from expedistill.prompts.registry import get as get_prompt
from expedistill.blocks.author import (
    gen_exam,
    render_skill_content,
    rewrite_skill,
    skill_markers,
    validate_skill_sections,
    write_skill,
)

STEPS_VIEW = "step 0: read config\nstep 1: restart the service"
WEAK_PROFILE = "A small model; known to skip intermediate steps."

DISTILL_SECTIONS = {
    "when_to_use": "When the service rejects a login.",
    "steps": ["Check the credentials in the config file.",
              "Restart the service."],
    "verify": "The service accepts the login.",
    "not_applicable_when": "The subtask is not about logging in.",
}

DISTILL_CONTENT = "\n".join([
    "[When to use] When the service rejects a login.",
    "[Steps]",
    "1. Check the credentials in the config file.",
    "2. Restart the service.",
    "[Verify] The service accepts the login.",
    "[Do not use when] The subtask is not about logging in.",
])

DIAGNOSE_SECTIONS = {
    "trigger": "You edit the config while the service is running.",
    "why_it_fails": "The running service overwrites your edit on restart.",
    "do_instead": ["Stop the service.", "Edit the config.",
                   "Start the service."],
    "not_applicable_when": "The subtask is not about the config.",
}

DIAGNOSE_CONTENT = "\n".join([
    "[Trigger] You edit the config while the service is running.",
    "[Why it fails] The running service overwrites your edit on restart.",
    "[Do instead]",
    "1. Stop the service.",
    "2. Edit the config.",
    "3. Start the service.",
    "[Do not use when] The subtask is not about the config.",
])

#: A heredoc-shaped steps item (the 2026-10-13 incident payload class: the
#: G2 prompts ask for the exact command form, and real commands span lines).
#: The continuation lines sit at column 0, exactly as the model wrote them.
HEREDOC_SECTIONS = {
    "when_to_use": "When you must hash a file without extra tools.",
    "steps": [
        "Check the file exists with `ls <file>`.",
        "Run this exact command:\npython3 - <<'PY'\nimport hashlib\n"
        "print(hashlib.sha256(open('<file>', 'rb').read()).hexdigest())\nPY",
        "Compare the printed digest with the expected value.",
    ],
    "verify": "The printed digest matches the expected value.",
    "not_applicable_when": "The file is not readable.",
}

HEREDOC_CONTENT = "\n".join([
    "[When to use] When you must hash a file without extra tools.",
    "[Steps]",
    "1. Check the file exists with `ls <file>`.",
    "2. Run this exact command:",
    "python3 - <<'PY'",
    "import hashlib",
    "print(hashlib.sha256(open('<file>', 'rb').read()).hexdigest())",
    "PY",
    "3. Compare the printed digest with the expected value.",
    "[Verify] The printed digest matches the expected value.",
    "[Do not use when] The file is not readable.",
])


def make_subtask(**overrides):
    task_id = make_task_id("tb", "task-42")
    trajectory_id = make_trajectory_id(task_id, "strong", 0)
    defaults = {
        "subtask_id": make_subtask_id(trajectory_id, 1),
        "task_id": task_id,
        "trajectory_id": trajectory_id,
        "source": "strong",
        "trajectory_success": True,
        "segment_index": 1,
        "step_range": (2, 4),
        "label": "Authenticate",
        "description": "Log in to the service with the stored credentials",
        "success": True,
        "context_summary": "The agent located the config file in step 0.",
    }
    defaults.update(overrides)
    return Subtask(**defaults)


class RecordingUsage:
    """Test double for the B7 UsageRecorder (same record signature)."""

    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def make_skill(subtask):
    # Prose content: a stored v2 skill never looks like this; it exists to
    # exercise rewrite_skill's marker-free tolerance branch.
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content="Check the config exists before reading it.",
        subtask_id=subtask.subtask_id,
    )


def make_marker_skill(subtask, content=DISTILL_CONTENT):
    # What a stored v2 skill actually looks like: renderer-made marker text.
    return Skill(
        skill_id=make_skill_id(subtask.subtask_id),
        content=content,
        subtask_id=subtask.subtask_id,
    )


# ---------------------------------------------------------------------------
# G2 write_skill
# ---------------------------------------------------------------------------

def test_write_skill_distill_wiring_and_fields():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script(DISTILL_SECTIONS))

    skill = write_skill(
        subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle,
        usage=RecordingUsage(),
    )

    # Exactly one call, with the SKILL_DISTILL template rendered from the
    # subtask fields (v2: no success variable, no context_summary).
    assert len(handle.client.calls) == 1
    expected = get_prompt("SKILL_DISTILL").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    assert skill.skill_id == make_skill_id(subtask.subtask_id)
    assert skill.subtask_id == subtask.subtask_id
    # The stored content is the renderer's marker text, not the model's
    # raw prose: injection, G7 and D8 all read this one representation.
    assert skill.content == DISTILL_CONTENT
    assert skill.version == 1
    assert skill.status == "pending"
    assert skill.rewrite_history == []
    assert skill.merged_from == []
    assert skill_markers(skill.content) == PROCEDURE_MARKERS


def test_write_skill_diagnose_wiring_and_fields():
    subtask = make_subtask(
        source="weak",
        trajectory_success=False,
        success=False,
    )
    handle = make_fake_handle(script=json_script(DIAGNOSE_SECTIONS))

    skill = write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "diagnose", handle)

    assert len(handle.client.calls) == 1
    expected = get_prompt("SKILL_DIAGNOSE").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
        context_summary=subtask.context_summary,
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected
    assert skill.content == DIAGNOSE_CONTENT
    assert skill_markers(skill.content) == FAILURE_REFERENCE_MARKERS


def test_write_skill_invalid_mode_raises_before_any_call():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script(DISTILL_SECTIONS))

    with pytest.raises(ValueError, match="distill"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "bogus", handle)

    assert handle.client.calls == []


def test_write_skill_distill_guard_rejects_failed_subtask():
    # Procedure material must be a succeeded subtask; an old v1 selection
    # rule feeding failures into the v2 prompt is refused loudly, before
    # any model call.
    subtask = make_subtask(
        source="strong",
        trajectory_success=False,
        success=False,
    )
    handle = make_fake_handle(script=json_script(DISTILL_SECTIONS))

    with pytest.raises(ValueError, match="requires subtask.success is True"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)

    assert handle.client.calls == []


def test_write_skill_diagnose_guard_rejects_succeeded_subtask():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script(DIAGNOSE_SECTIONS))

    with pytest.raises(ValueError, match="requires subtask.success is False"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "diagnose", handle)

    assert handle.client.calls == []


def test_write_skill_non_object_json_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script(["not", "an", "object"]))

    with pytest.raises(ModelOutputError, match="JSON object"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)


def test_write_skill_missing_field_raises():
    subtask = make_subtask()
    payload = dict(DISTILL_SECTIONS)
    del payload["verify"]
    handle = make_fake_handle(script=json_script(payload))

    with pytest.raises(ModelOutputError, match="verify"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)


def test_write_skill_empty_checklist_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        **DISTILL_SECTIONS, "steps": []}))

    with pytest.raises(ModelOutputError, match="steps"):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)


def test_write_skill_strips_self_numbered_checklist_items():
    # The G2 prompts forbid self-numbering; a stray "1. " is stripped by the
    # renderer, never double-numbered.
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        **DISTILL_SECTIONS,
        "steps": ["1. Check the credentials in the config file.",
                  "2) Restart the service."],
    }))

    skill = write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)

    lines = skill.content.split("\n")
    assert lines[2] == "1. Check the credentials in the config file."
    assert lines[3] == "2. Restart the service."


@pytest.mark.parametrize(
    "field, value",
    [
        ("when_to_use", ""),                       # empty string field
        ("when_to_use", "   "),                    # whitespace-only field
        ("verify", 7),                             # non-string field
        ("steps", [""]),                           # empty checklist item
        ("steps", ["1. "]),                        # self-numbering only
        ("steps", ["   "]),                        # whitespace-only item
        ("steps", [7]),                            # non-string item
        ("steps", "not a list"),                   # checklist wrong type
        ("when_to_use", "[Trigger] forged"),       # forged section marker
        ("steps", ["[Steps] do x"]),               # forged marker on an item
        ("when_to_use", "line one\n[Trigger] forged"),  # forged on a LATER line
        ("steps", ["do x\n[Verify] forged"]),      # forged line inside an item
    ],
)
def test_write_skill_bad_fields_raise(field, value):
    # Field-level failures are terminal (gen_exam precedent): no in-block
    # retry, the unit lands in the model_output_failed ledger. Multi-line
    # values are NOT on this list since the 2026-10-13 incident: the newline
    # rejection killed legal heredoc-shaped commands. The marker defense
    # moved to a per-line check, so a forged marker on ANY line still raises.
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        **DISTILL_SECTIONS, field: value}))

    with pytest.raises(ModelOutputError):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)


@pytest.mark.parametrize(
    "field, value",
    [
        ("trigger", "line one\n[Steps] forged"),    # forged on a LATER line
        ("do_instead", ["do x\n[Verify] forged"]),  # forged line inside item
    ],
)
def test_write_skill_bad_fields_raise_diagnose(field, value):
    # The same per-line marker defense for the diagnose mode's contract
    # (the distill table above exercises one mode; both modes' fields are
    # checked with the same rule).
    subtask = make_subtask(
        source="weak",
        trajectory_success=False,
        success=False,
    )
    handle = make_fake_handle(script=json_script({
        **DIAGNOSE_SECTIONS, field: value}))

    with pytest.raises(ModelOutputError):
        write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "diagnose", handle)


def test_write_skill_accepts_multiline_steps_verbatim():
    # The 2026-10-13 incident regression: a heredoc-shaped steps item is
    # legal payload. The renderer numbers the item's FIRST line and writes
    # every continuation line at column 0, byte-for-byte as the model wrote
    # it; the stored content still reads back as the procedure marker set.
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script(HEREDOC_SECTIONS))

    skill = write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)

    assert skill.content == HEREDOC_CONTENT
    lines = skill.content.split("\n")
    assert lines[3] == "2. Run this exact command:"
    assert lines[4] == "python3 - <<'PY'"     # continuation at column 0
    assert lines[7] == "PY"                   # heredoc closer, column 0
    assert skill_markers(skill.content) == PROCEDURE_MARKERS
    # Determinism: same sections in, byte-identical content out.
    assert render_skill_content("distill", HEREDOC_SECTIONS) == skill.content
    assert (render_skill_content("distill", HEREDOC_SECTIONS)
            == render_skill_content("distill", HEREDOC_SECTIONS))


def test_write_skill_accepts_multiline_scalar():
    # A scalar field may span lines too; its continuation lines land at
    # column 0 and the marker set stays readable.
    subtask = make_subtask()
    sections = {
        **DISTILL_SECTIONS,
        "verify": "The service log shows:\nLOGIN OK for user <user>\n"
                  "and no retry line.",
    }
    handle = make_fake_handle(script=json_script(sections))

    skill = write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle)

    assert skill.content == "\n".join([
        "[When to use] When the service rejects a login.",
        "[Steps]",
        "1. Check the credentials in the config file.",
        "2. Restart the service.",
        "[Verify] The service log shows:",
        "LOGIN OK for user <user>",
        "and no retry line.",
        "[Do not use when] The subtask is not about logging in.",
    ])
    assert skill_markers(skill.content) == PROCEDURE_MARKERS


def test_crlf_and_lone_cr_are_normalized_everywhere():
    # A copy-pasted CRLF (or a lone CR) must not survive into the stored
    # content: validation and rendering share _normalize_newlines, so the
    # checked bytes and the written bytes are the same.
    sections = {
        **DISTILL_SECTIONS,
        "when_to_use": "When the service rejects a login.\r\nSecond line.",
        "steps": ["Check the credentials.\rRestart the service."],
    }
    validate_skill_sections("distill", sections)
    content = render_skill_content("distill", sections)

    assert "\r" not in content
    assert content.split("\n") == [
        "[When to use] When the service rejects a login.",
        "Second line.",
        "[Steps]",
        "1. Check the credentials.",
        "Restart the service.",
        "[Verify] The service accepts the login.",
        "[Do not use when] The subtask is not about logging in.",
    ]
    assert skill_markers(content) == PROCEDURE_MARKERS


def test_write_skill_passes_usage_through():
    subtask = make_subtask()
    usage = RecordingUsage()
    handle = make_fake_handle(script=json_script(DISTILL_SECTIONS))

    write_skill(subtask, STEPS_VIEW, WEAK_PROFILE, "distill", handle, usage=usage)

    assert len(usage.records) == 1
    assert usage.records[0]["prompt"] == "SKILL_DISTILL"
    assert usage.records[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# render_skill_content / validate_skill_sections / skill_markers
# ---------------------------------------------------------------------------

def test_render_skill_content_distill_is_exact_marker_text():
    lines = render_skill_content("distill", DISTILL_SECTIONS).split("\n")
    assert lines == [
        "[When to use] When the service rejects a login.",
        "[Steps]",
        "1. Check the credentials in the config file.",
        "2. Restart the service.",
        "[Verify] The service accepts the login.",
        "[Do not use when] The subtask is not about logging in.",
    ]


def test_render_skill_content_diagnose_is_exact_marker_text():
    lines = render_skill_content("diagnose", DIAGNOSE_SECTIONS).split("\n")
    assert lines == [
        "[Trigger] You edit the config while the service is running.",
        "[Why it fails] The running service overwrites your edit on restart.",
        "[Do instead]",
        "1. Stop the service.",
        "2. Edit the config.",
        "3. Start the service.",
        "[Do not use when] The subtask is not about the config.",
    ]


def test_render_is_deterministic():
    # Same sections in, byte-identical content out (decision section 4.2's
    # stage-3 invariate), for single-line AND multi-line payloads alike
    # (the 2026-10-14 fix extends the invariant to heredoc-shaped values).
    multiline_diagnose = {
        **DIAGNOSE_SECTIONS,
        "do_instead": ["Stop the service.\nsystemctl stop <service>",
                       "Edit the config."],
    }
    for mode, sections in (("distill", DISTILL_SECTIONS),
                           ("diagnose", DIAGNOSE_SECTIONS),
                           ("distill", HEREDOC_SECTIONS),
                           ("diagnose", multiline_diagnose)):
        assert (render_skill_content(mode, sections)
                == render_skill_content(mode, sections))


def test_validate_skill_sections_accepts_legal_payloads():
    validate_skill_sections("distill", DISTILL_SECTIONS)
    validate_skill_sections("diagnose", DIAGNOSE_SECTIONS)
    # Extra keys are ignored (lenient read, gen_exam precedent)...
    validate_skill_sections("distill", {**DISTILL_SECTIONS, "extra": "x"})
    # ...but the checklist type check is never skipped.
    with pytest.raises(ModelOutputError, match="steps"):
        validate_skill_sections(
            "distill", {**DISTILL_SECTIONS, "steps": "1. not a list"})


def test_validate_skill_sections_non_dict_raises():
    with pytest.raises(ModelOutputError, match="JSON object"):
        validate_skill_sections("distill", ["not", "an", "object"])


def test_render_and_validate_reject_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        render_skill_content("bogus", DISTILL_SECTIONS)
    with pytest.raises(ValueError, match="mode"):
        validate_skill_sections("bogus", DISTILL_SECTIONS)


def test_skill_markers_reads_complete_sets():
    assert skill_markers(DISTILL_CONTENT) == PROCEDURE_MARKERS
    assert skill_markers(DIAGNOSE_CONTENT) == FAILURE_REFERENCE_MARKERS


def test_skill_markers_partial_set_is_none():
    partial = "\n".join(
        line for line in DISTILL_CONTENT.split("\n")
        if not line.startswith("[Verify]"))
    assert "[Verify]" not in partial
    assert skill_markers(partial) is None


def test_skill_markers_mixed_sets_are_none():
    # A failure-only marker forged into a procedure skill leaves the shared
    # "[Do not use when]" unable to disambiguate: neither set is complete
    # and clean, so the content reads as None.
    mixed = DISTILL_CONTENT + "\n[Trigger] forged"
    assert skill_markers(mixed) is None
    both = DISTILL_CONTENT + "\n" + DIAGNOSE_CONTENT
    assert skill_markers(both) is None


def test_skill_markers_is_line_start_anchored():
    # Every marker present but mid-line -- exactly what the single-line
    # compressed injection block looks like: no set is read off it.
    one_line = " ".join(DISTILL_CONTENT.split("\n"))
    for marker in PROCEDURE_MARKERS:
        assert marker in one_line
    assert skill_markers(one_line) is None
    # Indented markers do not anchor either: the marker must sit at
    # column 0, so a bolded or indented variant reads as None.
    indented = "\n".join("    " + line
                         for line in DISTILL_CONTENT.split("\n"))
    assert skill_markers(indented) is None


def test_render_and_skill_markers_roundtrip():
    assert skill_markers(
        render_skill_content("distill", DISTILL_SECTIONS)) \
        == PROCEDURE_MARKERS
    assert skill_markers(
        render_skill_content("diagnose", DIAGNOSE_SECTIONS)) \
        == FAILURE_REFERENCE_MARKERS


# ---------------------------------------------------------------------------
# G3 gen_exam
# ---------------------------------------------------------------------------

def test_gen_exam_wiring_and_fields():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": "A service rejects your login. What do you check first?",
        "reference_answer": "Whether the credentials in the config are valid.",
    }))

    exam = gen_exam(subtask, STEPS_VIEW, handle)

    assert len(handle.client.calls) == 1
    expected = get_prompt("EXAM_GEN").format(
        label=subtask.label,
        description=subtask.description,
        steps_view=STEPS_VIEW,
    )
    assert handle.client.calls[0]["user"] == expected

    assert exam.exam_id == make_exam_id(subtask.subtask_id)
    assert exam.subtask_id == subtask.subtask_id
    assert exam.question_text == "A service rejects your login. What do you check first?"
    assert exam.reference_answer == "Whether the credentials in the config are valid."


def test_gen_exam_empty_question_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": "",
        "reference_answer": "valid reference",
    }))

    with pytest.raises(ModelOutputError, match="question_text"):
        gen_exam(subtask, STEPS_VIEW, handle)


def test_gen_exam_missing_reference_answer_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({"question_text": "q"}))

    with pytest.raises(ModelOutputError, match="reference_answer"):
        gen_exam(subtask, STEPS_VIEW, handle)


def test_gen_exam_non_string_field_raises():
    subtask = make_subtask()
    handle = make_fake_handle(script=json_script({
        "question_text": 7,
        "reference_answer": "r",
    }))

    with pytest.raises(ModelOutputError, match="question_text"):
        gen_exam(subtask, STEPS_VIEW, handle)


# ---------------------------------------------------------------------------
# G6 rewrite_skill
# ---------------------------------------------------------------------------

def test_rewrite_skill_wiring_and_result():
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "missing_step",
        "skill_text": "  Do X. Then do Y explicitly.  ",
        "rewrite_reason": "Spelled out the skipped intermediate step.",
    }))

    result = rewrite_skill(
        skill,
        question_text="What should you do when X?",
        weak_answer="I would do X.",
        error_description="The answer skips the verification step.",
        weak_profile=WEAK_PROFILE,
        handle=handle,
    )

    assert len(handle.client.calls) == 1
    expected = get_prompt("REWRITE").format(
        skill_text=skill.content,
        question_text="What should you do when X?",
        weak_answer="I would do X.",
        error_description="The answer skips the verification step.",
        weak_profile=WEAK_PROFILE,
    )
    assert handle.client.calls[0]["user"] == expected

    assert result == {
        "content": "Do X. Then do Y explicitly.",
        "failure_category": "missing_step",
        "rewrite_reason": "Spelled out the skipped intermediate step.",
    }

    # The block must not mutate the Skill: composites own version bumps and
    # rewrite_history appends.
    assert skill.content == "Check the config exists before reading it."
    assert skill.version == 1
    assert skill.rewrite_history == []


def test_rewrite_skill_keeps_the_marker_set():
    subtask = make_subtask()
    skill = make_marker_skill(subtask)
    rewritten = render_skill_content("distill", {
        **DISTILL_SECTIONS,
        "steps": ["Check the credentials before every login."],
        "verify": "The login succeeds on the first try.",
    })
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": rewritten,
        "rewrite_reason": "Simplified the trigger wording.",
    }))

    result = rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)

    assert result["content"] == rewritten
    assert result["failure_category"] == "misread"
    assert skill_markers(result["content"]) == PROCEDURE_MARKERS


def test_rewrite_skill_dropping_the_markers_raises():
    # The rewrite must keep the four sections, not collapse them into
    # prose: prose in the bank would silently disarm every marker-based
    # consumer (K5 isochrony check, retrieval routing).
    subtask = make_subtask()
    skill = make_marker_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": "Do X when Y, in plain prose.",
        "rewrite_reason": "Collapsed the sections.",
    }))

    with pytest.raises(ModelOutputError, match="bracket sections"):
        rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)


def test_rewrite_skill_switching_marker_kind_raises():
    # Set equality, not substring containment: a rewrite that swaps the
    # procedure sections for failure-reference sections is rejected even
    # though it carries four markers.
    subtask = make_subtask()
    skill = make_marker_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": DIAGNOSE_CONTENT,
        "rewrite_reason": "Rewrote it as a failure reference.",
    }))

    with pytest.raises(ModelOutputError, match="bracket sections"):
        rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)


def test_rewrite_skill_unmarked_original_skips_the_marker_check():
    # test/foreign-content tolerance (unreachable in a pure v2 run): an
    # original without markers is rewritten without a marker check, prose
    # in, prose out.
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": "Do X when Y, still plain prose.",
        "rewrite_reason": "Simplified.",
    }))

    result = rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)

    assert result["content"] == "Do X when Y, still plain prose."


@pytest.mark.parametrize("category", ["catastrophe", "", None, 3])
def test_rewrite_skill_invalid_category_raises(category):
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": category,
        "skill_text": "rewritten",
        "rewrite_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="failure_category"):
        rewrite_skill(
            skill, "q", "a", "err", WEAK_PROFILE, handle
        )


def test_rewrite_skill_empty_skill_text_raises():
    subtask = make_subtask()
    skill = make_skill(subtask)
    handle = make_fake_handle(script=json_script({
        "failure_category": "misread",
        "skill_text": "   ",
        "rewrite_reason": "reason",
    }))

    with pytest.raises(ModelOutputError, match="skill_text"):
        rewrite_skill(skill, "q", "a", "err", WEAK_PROFILE, handle)
