"""A fake ALFWorld adapter speaking the documented line protocol (budget 3).

The tests substitute it for ``ADAPTER_SOURCE`` so the env and plugin layers
run without the eval venv. Every action but "win" is echoed; "win" solves
the task and the third action ends the episode on the budget. With
``faults`` the adapter also misbehaves on request: "die" exits without
replying, "hang" stalls past any read deadline, "garbage" breaks the
protocol.
"""

__all__ = ["fake_adapter_source"]

_HEAD = '''\
import json
import sys
%(import_time)s
def emit(tag, payload):
    sys.stdout.write(tag + " " + json.dumps(payload) + "\\n")
    sys.stdout.flush()

ADMISSIBLE = ["go to fridge 1", "look", "win"]

def obs(text, won=False, done=False, reason=None):
    return {"text": text, "admissible": ADMISSIBLE,
            "won": won, "done": done, "reason": reason}

BUDGET = 3
emit("H", {"budget": BUDGET})
emit("O", obs(%(first)s))
steps = 0
while True:
    line = sys.stdin.readline()
    if not line:
        break
    action = line.strip()
    if not action:
        continue
'''

_FAULTS = '''\
    if action == "die":
        sys.stderr.write("fake adapter dying on request\\n")
        sys.exit(3)
    if action == "hang":
        time.sleep(30)
    if action == "garbage":
        sys.stdout.write("this line breaks the protocol\\n")
        sys.stdout.flush()
        continue
'''

_TAIL = '''\
    steps += 1
    if action == "win":
        emit("O", obs("Task is SOLVED.", won=True, done=True, reason="won"))
        break
    if steps >= BUDGET:
        emit("O", obs("you did: " + action, done=True, reason="budget"))
        break
    emit("O", obs("you did: " + action))
'''


def fake_adapter_source(first_observation: str, faults: bool = False) -> str:
    """The adapter's source; ``first_observation`` is the text of its first
    observation, a Python string-literal expression (for example
    ``'"You are in a room."'``)."""
    head = _HEAD % {"import_time": "import time\n" if faults else "",
                    "first": first_observation}
    return head + (_FAULTS if faults else "") + _TAIL
