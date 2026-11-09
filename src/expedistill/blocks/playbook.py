"""G13: write a benchmark's global playbook from baseline facts.

The block is generic: the plugin supplies the prompt (its
``playbook_prompt_name``) and the facts text (its ``playbook_facts``); this
function fills the prompt, takes the playbook out of the reply and returns
it. Disk, resume and the choice of facts belong to the callers.

Output contract (the prompt ends with the same shape): the reply ends with
the playbook between ``<playbook>`` and ``</playbook>``. A reasoning model's
visible reply holds drafts and talk about the tags before its answer, so the
answer is read from the END: the span from the last opening tag to the
closing tag that ends the reply. A reply that breaks the contract is never
repaired by guessing which draft to keep or by cutting a long text; the
reason is handed back to the model, which writes the playbook again.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from expedistill.infra.reliability import ModelOutputError
from expedistill.llm.call import ModelHandle, call_model
from expedistill.prompts.profile import profile_section

__all__ = ["MAX_PLAYBOOK_CHARS", "MIN_PLAYBOOK_CHARS", "extract_playbook",
           "missing_requirements", "write_playbook"]

#: The agent reads the playbook at every step, so its size is bounded by
#: contract (the prompt states the limit). The hand-written reference
#: playbook is about 5k characters.
MAX_PLAYBOOK_CHARS = 20000
#: Below this size the span is a placeholder, not a playbook.
MIN_PLAYBOOK_CHARS = 300
#: Tries in total: the first ask plus corrections.
_ATTEMPTS = 2

_OPEN = "<playbook>"
_CLOSE = "</playbook>"


def extract_playbook(reply: str) -> str:
    """The playbook of one reply, or ``ValueError`` naming what is wrong."""
    text = str(reply).rstrip()
    if not text.endswith(_CLOSE):
        raise ValueError(
            f"the reply does not end with the closing {_CLOSE} tag; "
            "nothing may follow it")
    start = text.rfind(_OPEN)
    if start < 0:
        raise ValueError(f"the reply has no opening {_OPEN} tag")
    body = text[start + len(_OPEN):-len(_CLOSE)]
    if _CLOSE in body:
        raise ValueError(
            f"a {_CLOSE} tag appears between the last opening tag and the "
            "end of the reply")
    body = body.strip()
    if len(body) < MIN_PLAYBOOK_CHARS:
        raise ValueError(
            f"the playbook has {len(body)} characters; it needs at least "
            f"{MIN_PLAYBOOK_CHARS}")
    if len(body) > MAX_PLAYBOOK_CHARS:
        raise ValueError(
            f"the playbook has {len(body)} characters; the limit is "
            f"{MAX_PLAYBOOK_CHARS}. Write a shorter playbook")
    return body


def missing_requirements(text: str, requirements: Any) -> List[str]:
    """The names of the requirements the text does not meet."""
    return [item.name for item in requirements if not item.matches(text)]


def write_playbook(
    handle: ModelHandle,
    facts: str,
    *,
    prompt_name: str,
    weak_profile: str = "",
    usage: Any = None,
    run_dir: Optional[Union[str, Path]] = None,
    requirements: Any = (),
    attempts: int = _ATTEMPTS,
    record: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """The playbook text from one model call, corrected if needed.

    ``weak_profile`` is the reader profile every writing prompt gets; with
    the deployment-constraint profile on it keeps the rules executable from
    what the agent sees at one step. The prompt must carry the variables
    ``facts``, ``profile_section`` and ``correction_note``.

    ``requirements`` are the benchmark's machine checks
    (``BenchmarkPlugin.playbook_requirements``). The write is one sampled
    call, and the same facts can yield a complete playbook or a partial
    one; a reply that breaks a requirement is sent back with the missing
    parts named, which turns a quality lottery into a bounded repair. With
    no requirements this is the old single-attempt shape.

    ``record``, when given, collects one entry per attempt (``chars`` and
    the ``missing`` requirement names) so a caller can report how much
    repairing the write needed.
    """
    notes: List[str] = []
    problem = "the writer produced no acceptable playbook"
    for attempt in range(1, attempts + 1):
        reply = call_model(
            handle, prompt_name,
            {"facts": facts,
             "profile_section": profile_section(
                 "playbook_reader", weak_profile,
                 handle.prompt_defaults.get("agent_kind")),
             "correction_note": "".join(notes)},
            usage=usage, run_dir=run_dir)
        try:
            text = extract_playbook(reply)
        except ValueError as exc:
            problem = str(exc)
            if record is not None:
                record.append({"attempt": attempt, "chars": 0,
                               "missing": [], "rejected": problem})
            notes.append("Your previous reply was rejected: " + problem
                         + ". Write the playbook again and end the reply "
                         "with the exact shape given above.\n")
            continue
        missing = missing_requirements(text, requirements)
        if record is not None:
            record.append({"attempt": attempt, "chars": len(text),
                           "missing": list(missing), "rejected": ""})
        if not missing:
            return text
        problem = "the playbook is missing: " + ", ".join(missing)
        notes.append(
            "Your previous playbook was rejected: " + problem
            + ". Write it again and make sure every one of those parts is "
            "present and explicit:\n"
            + "\n".join(f"- {item.name}: {item.describe}"
                        for item in requirements if item.name in missing)
            + "\n")
    raise ModelOutputError(f"{prompt_name}: {problem}")
