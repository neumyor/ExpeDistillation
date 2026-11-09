"""The learned-playbook step: a delivery-level switch, off by default.

A delivery asking for ``playbook_source: learned`` makes the learn phase
after the baseline write ``playbook.md`` from the baseline sheets through
the benchmark plugin's two hooks. Without the key nothing is written and no
model call is made.
"""

from types import SimpleNamespace

import pytest

from expedistill.benchmarks.base import BenchmarkPlugin
from expedistill.composites import playbook_store
from expedistill.core.config import ExecutePhaseSpec, PHASE_ROLE_BASELINE
from expedistill.infra.reliability import ConfigError, ModelOutputError
from expedistill.llm import make_fake_handle
from expedistill.stages import learn_playbook
from tests.helpers.traces import baseline_sheet

PROMPT = "ALFWORLD_PLAYBOOK_WRITE"
PLAY = ("Search every place once. " * 20).strip()


@pytest.fixture(autouse=True)
def _prompt_registered():
    from tests.helpers.plugins import ensure_plugin
    ensure_plugin("alfworld")


class _Plugin(BenchmarkPlugin):
    """Only what ensure_playbook touches."""

    playbook_prompt_name = PROMPT

    def __init__(self):
        self.seen = []

    name = "fakebench"

    def load_tasks(self, tasks_spec):
        return []

    def run(self, task, handle, skills_block, workdir):
        raise AssertionError("not executed")

    @property
    def view_rules(self):
        raise AssertionError("not used")

    @property
    def task_prompt_name(self):
        return "x"

    def playbook_facts(self, sheets):
        self.seen.append([sheet.sheet_id for sheet in sheets])
        return "FACTS:" + ",".join(sorted(s.sheet_id for s in sheets))


def _patch_plugin(monkeypatch, plugin):
    monkeypatch.setattr(learn_playbook, "get_plugin", lambda name: plugin)


def _ctx(tmp_path, handle):
    return SimpleNamespace(
        run_dir=tmp_path, usage=None, handles={"author": handle},
        config=SimpleNamespace(benchmark="fakebench"))


def _phase(index=1, name="learn_add"):
    return SimpleNamespace(index=index, name=name,
                           label=f"p{index + 1:02d}:{name}")


def _report():
    sheets = [baseline_sheet("t1", "weak", 0, benchmark="fakebench"),
              baseline_sheet("t1", "strong", 0, benchmark="fakebench")]
    return SimpleNamespace(sheets=sheets)


def test_the_playbook_is_written_from_the_baseline_sheets(
        tmp_path, monkeypatch):
    plugin = _Plugin()
    _patch_plugin(monkeypatch, plugin)
    handle = make_fake_handle(
        "author", ["<playbook>\n  " + PLAY + "  \n</playbook>"])
    wrote = learn_playbook.ensure_playbook(
        _ctx(tmp_path, handle), _phase(), _report(), "profile text")
    assert wrote is True
    path = playbook_store.playbook_path(tmp_path, "p02:learn_add")
    assert path.read_text(encoding="utf-8") == PLAY
    user = handle.client.calls[0]["user"]
    assert "FACTS:" in user and "profile text" in user
    assert len(plugin.seen) == 1


def test_an_existing_playbook_is_not_written_again(tmp_path, monkeypatch):
    plugin = _Plugin()
    _patch_plugin(monkeypatch, plugin)
    playbook_store.write_learned_playbook(
        tmp_path, "p02:learn_add", "KEPT", facts_chars=1)
    handle = make_fake_handle("author", [])
    wrote = learn_playbook.ensure_playbook(
        _ctx(tmp_path, handle), _phase(), _report(), "")
    assert wrote is False
    assert handle.client.calls == []
    assert plugin.seen == []
    assert playbook_store.playbook_path(
        tmp_path, "p02:learn_add").read_text() == "KEPT"


def test_a_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    _patch_plugin(monkeypatch, _Plugin())
    handle = make_fake_handle("author", ["<playbook></playbook>"] * 4)
    with pytest.raises(ModelOutputError):
        learn_playbook.ensure_playbook(
            _ctx(tmp_path, handle), _phase(), _report(), "")
    assert not playbook_store.playbook_path(
        tmp_path, "p02:learn_add").exists()


def _config(delivery):
    spec = ExecutePhaseSpec(role="test", delivery=delivery,
                            execution={"name": "real"})
    base = ExecutePhaseSpec(role=PHASE_ROLE_BASELINE,
                            delivery={"name": "none"},
                            execution={"name": "real"})
    return SimpleNamespace(phases={"baseline": base, "test": spec})


def test_requested_only_for_the_learned_source():
    assert learn_playbook.requested(_config(
        {"name": "folder", "playbook_source": "learned"}))
    assert not learn_playbook.requested(_config({"name": "folder"}))
    assert not learn_playbook.requested(_config(
        {"name": "folder", "playbook_file": "x.md"}))


def test_the_default_plugin_refuses_the_capability():
    class Bare(_Plugin):
        playbook_prompt_name = None

        def playbook_facts(self, sheets):
            return BenchmarkPlugin.playbook_facts(self, sheets)

    with pytest.raises(ConfigError, match="no learned-playbook"):
        Bare().playbook_facts([])
