"""The TB skill-folder fixtures shared by the sandbox and read-suffix tests:
a two-skill bank, its manifest and catalog, the materialised folder, and the
trial files of an agent that cats the catalog and one skill inside the
sandbox mount."""

import json

from expedistill.composites.skill_materialize import materialize_skill_folder
from expedistill.core.ids import make_type_id
from expedistill.inject.skill_text import render_card
from expedistill.methods.delivery.folder_delivery import skill_folder_manifest
from tests.helpers.skills import make_skill
from tests.helpers.tb_fake_remote import TRIAL, completed_trial_files

BANK = [
    make_skill(make_type_id(1), "The service rejects connections."),
    make_skill(make_type_id(2), "Check the log before restarting.",
               version=3),
]
MANIFEST = skill_folder_manifest(BANK)
CATALOG = MANIFEST["catalog_text"]


def materialized(tmp_path):
    return materialize_skill_folder(MANIFEST, BANK,
                                    tmp_path / "skill_folder")


#: A trajectory whose agent cats the catalog and one skill file inside
#: the sandbox mount -- the raw material M4's read audit parses. The
#: step shape is Harbor's (benchmarks/tb/trajectory.py's frozen input).
CAT_TRAJECTORY = {
    "schema_version": "harbor-trajectory-1",
    "session_id": "sess-folder",
    "agent": {"model_name": "qwen3.6-flash-distill"},
    "steps": [
        {"step_id": 1, "source": "user",
         "message": "TASK: train a fasttext model."},
        {"step_id": 2, "source": "agent",
         "tool_calls": [{"tool_call_id": "c1", "function_name": "bash_command",
                         "arguments": {"keystrokes":
                                       "cat /opt/openclaw-skills/"
                                       "train-fasttext/catalog.md\n"}}],
         "observation": {"results": [{"content": CATALOG}]}},
        {"step_id": 3, "source": "agent",
         "tool_calls": [{"tool_call_id": "c2", "function_name": "bash_command",
                         "arguments": {"keystrokes":
                                       "cat /opt/openclaw-skills/"
                                       "train-fasttext/"
                                       "type-001-skill/"
                                       "SKILL.md\n"}}],
         "observation": {"results": [{"content":
                                      render_card(BANK[0])}]}},
    ],
    "final_metrics": {"total_prompt_tokens": 100,
                      "total_completion_tokens": 20,
                      "total_cached_tokens": 0},
}


def trial_files_with_cat():
    files = completed_trial_files()
    files[f"{TRIAL}/agent/trajectory.json"] = json.dumps(CAT_TRAJECTORY)
    return files


