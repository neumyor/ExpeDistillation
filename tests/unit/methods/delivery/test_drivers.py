"""Contract tests for the delivery drivers' frozen signature
(``resolve(bank, tasks, params) -> ExecutionUnitList``, the only form
the phase drivers call).

Also pins the bank-membership filter the whole-bank and folder drivers
apply: the phase artifacts carry active AND discarded members, and only
the active cards are delivered.
"""

from expedistill.core.ids import make_task_id, make_type_id
from expedistill.core.models import Skill
from expedistill.inject import bank_reference, format_skills_block
from expedistill.methods import resolve_fixed
from tests.helpers.skills import make_skill

BENCHMARK = "tb"

MARKED = {
    "steps": ["Check the service status.", "Restart the service."],
    "not_applicable_when": "The status command already reports running.",
}


def _card(serial, status="active"):
    return make_skill(make_type_id(serial),
                      f"The service {serial} rejects connections.",
                      status=status, **MARKED)


def _bank():
    """A phase artifact's worth of skills: one active, one discarded."""
    active = _card(1)
    discarded = _card(2, status="discarded")
    return [active, discarded], active


def test_whole_bank_injects_the_active_members_only():
    bank, active = _bank()
    tasks = [make_task_id(BENCHMARK, f"t{i}") for i in (1, 2, 3)]
    delivery, params = resolve_fixed("delivery", "whole_bank")
    rows = delivery.resolve(bank, tasks, params)
    # Every row carries the ACTIVE bank's digest and its rendered block;
    # the discarded member never enters the injection.
    assert len(rows) == 3
    for row in rows:
        assert row.skills_injected == bank_reference([active])
        assert row.skills_block == format_skills_block([active])


def test_a_bank_of_only_discarded_members_injects_nothing():
    tasks = [make_task_id(BENCHMARK, "t1")]
    whole, whole_params = resolve_fixed("delivery", "whole_bank")
    rows = whole.resolve([_card(1, status="discarded")], tasks, whole_params)
    assert rows[0].skills_block == format_skills_block([])
    assert rows[0].skills_injected == bank_reference([])


def test_folder_lists_the_active_members_only():
    bank, active = _bank()
    delivery, params = resolve_fixed("delivery", "folder")
    rows = delivery.resolve(bank, ["t1"], params)
    manifest = rows[0].aux["skill_folder"]
    assert [entry["skill_id"] for entry in manifest["files"]] == [
        active.skill_id]
    assert rows[0].skills_injected == bank_reference([active])
    # The fingerprint names the whole artifact the folder resolves
    # against (the materializer checks the bank against it).
    assert manifest["bank_fingerprint"] == bank_reference(bank)


def test_folder_rows_of_an_empty_bank_still_carry_the_manifest():
    delivery, params = resolve_fixed("delivery", "folder")
    rows = delivery.resolve([_card(1, status="discarded")], ["t1", "t2"],
                            params)
    for row in rows:
        manifest = row.aux["skill_folder"]
        assert manifest["files"] == []
        assert manifest["catalog_text"] == ""
        assert row.skills_block == ""


def test_none_supplies_the_bare_rows():
    # The bank and tasks are the family's operands and the bank is
    # ignored: there is nothing to select from. skills_injected is None
    # (the bare control's record), never []; one row per task.
    delivery, params = resolve_fixed("delivery", "none")
    rows = delivery.resolve(None, ["task-a", "task-b"], params)
    assert [(row.task_ref, row.skills_injected, row.skills_block)
            for row in rows] == [
        ("task-a", None, ""), ("task-b", None, "")]


# ---------------------------------------------------------------------------
# whole_bank verbatim
# ---------------------------------------------------------------------------

def test_whole_bank_verbatim_injects_the_imported_text_as_is():
    from expedistill.methods.delivery import whole_bank
    from expedistill.methods.delivery.params import WholeBankParams
    document = "# Rules\n\n- one\n- two"
    skill = Skill(skill_id="import/x/rules", subtask_type="",
                  source_subtasks=[], summary="Rules.", when_to_use="",
                  steps=[], not_applicable_when="", verbatim_text=document)
    plain = whole_bank.resolve([skill], ["t1"], WholeBankParams())
    verbatim = whole_bank.resolve(
        [skill], ["t1"], WholeBankParams(verbatim=True))
    assert verbatim[0].skills_block == "## Skill rules\n\n" + document
    assert plain[0].skills_block != verbatim[0].skills_block
    # The recorded bank identity does not depend on how the text is shown.
    assert plain[0].skills_injected == verbatim[0].skills_injected


def test_whole_bank_verbatim_must_be_a_bool():
    import pytest
    from expedistill.core.config import ConfigError
    from expedistill.methods import resolve_params
    with pytest.raises(ConfigError, match="verbatim"):
        resolve_params("delivery", {"name": "whole_bank", "verbatim": "yes"})
    assert resolve_params(
        "delivery", {"name": "whole_bank", "verbatim": True}).verbatim is True
