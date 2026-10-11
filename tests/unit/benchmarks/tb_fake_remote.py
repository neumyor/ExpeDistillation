"""A stateful fake of the TB remote host, shared by the TB plugin tests and
the K1 composite tests.

Unlike the scripted ``FakeRemoteLink`` in test_tb_plugin.py, this fake keeps
a remote file tree and a process table and answers the plugin's probe/poll
commands from that state, so a result left under one run id is visible to
exactly the commands that name that run id -- which is what the run-id
collision tests need. No network, no ssh.
"""

import re
from pathlib import Path

from expedistill.infra.reliability import InfrastructureError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tb"
ROOT = "/data2/niuyiming/expedistill"
JOBS = f"{ROOT}/jobs"

_RUN_ID_IN_JOBS_RE = re.compile(r"/jobs/([A-Za-z0-9._-]+)/")
#: The plugin's identity check: /proc/<pid>/environ must hold RUN_ID=<id>.
_OURS_RE = re.compile(
    r"/proc/([0-9]+)/environ.*?RUN_ID=([A-Za-z0-9._-]+)")
_TERM_RE = re.compile(r"kill -TERM (?:-- -)?([0-9]+)")
_ENV_RE = {
    "run_id": re.compile(r"\bRUN_ID=(\S+)"),
    "task": re.compile(r"\bTBENCH_TASK_NAMES=(\S+)"),
    "model": re.compile(r"\bMODEL_NAME=(\S+)"),
}


class SimulatedCrash(BaseException):
    """Stands in for the local process dying mid-poll (not an Exception, so
    nothing in the plugin can swallow it)."""


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def trial_files(run_id, task, result="result_success.json",
                verifier_log="verifier_collected.txt", trial="AbCdEf1"):
    """Remote files of one finished Harbor trial under ``run_id``."""
    trial_dir = f"{JOBS}/{run_id}/{task}__{trial}"
    return {
        f"{trial_dir}/result.json": fixture_text(result),
        f"{trial_dir}/agent/trajectory.json": fixture_text("trajectory.json"),
        f"{trial_dir}/verifier/test-stdout.txt": fixture_text(verifier_log),
    }


class JobsRemoteLink:
    """Remote host double: file tree + live pid set + a launch log.

    ``on_submit(run_id, task, model) -> dict | None`` decides what a launched
    runner leaves behind: a dict of remote files means "the trial finished
    with these files" (the pid is then dead); None means "still running"
    (the pid stays alive).
    """

    def __init__(self, files=None, alive=(), on_submit=None,
                 home="/home/node29"):
        self.host = "user@node29"
        self.key_path = "/tmp/fake-key"
        self.root = ROOT
        self.home = home
        self.files = dict(files or {})
        #: Live pids on the host. ``owner`` maps the ones our runners hold to
        #: their RUN_ID; a live pid without an owner is somebody else's
        #: process (e.g. one that reused a recorded pid).
        self.alive = set(alive)
        self.owner = {}
        self.on_submit = on_submit or (lambda run_id, task, model: trial_files(
            run_id, task))
        self.commands = []
        self.launches = []
        self.puts = []
        self.kills = []
        #: Number of upcoming polls that "crash" the local process.
        self.crash_polls = 0
        self._next_pid = 5000

    # -- RemoteLink surface ------------------------------------------------

    def run(self, cmd, timeout=None):
        self.commands.append(cmd)
        assert "pkill" not in cmd and "pgrep" not in cmd, cmd
        if "$HOME" in cmd:
            return self.home, "", 0
        if "nohup" in cmd:
            return self._launch(cmd)
        if "result.json" in cmd:
            return self._check(cmd), "", 0
        if "tail -c" in cmd:
            return "", "", 0
        term = _TERM_RE.search(cmd)
        if term is not None:
            # check-then-kill: the signal is sent only if the identity check
            # in the same command passes.
            if not self._ours(cmd):
                return "NOT_OURS\n", "", 0
            pid = int(term.group(1))
            self.kills.append(pid)
            self.alive.discard(pid)
            return "KILLED 0\n", "", 0
        return "", "", 0  # mkdir, partial-trajectory lookup, ...

    def get(self, remote, local, timeout=None):
        try:
            content = self.files[str(remote)]
        except KeyError:
            raise InfrastructureError(
                f"scp failed (rc=1) get {remote} -> {local}")
        Path(local).write_text(content, encoding="utf-8")

    def put(self, local, remote, timeout=None):
        self.puts.append((local, str(remote)))
        self.files[str(remote)] = Path(local).read_text(encoding="utf-8")

    # -- helpers -----------------------------------------------------------

    def submits(self):
        return [cmd for cmd in self.commands if "nohup" in cmd]

    def finish(self, run_id, task="train-fasttext", **trial_kwargs):
        """The launched runner of ``run_id`` completes with a trial."""
        self.files.update(trial_files(run_id, task, **trial_kwargs))
        for launch in self.launches:
            if launch["run_id"] == run_id:
                self.alive.discard(launch["pid"])

    def _launch(self, cmd):
        fields = {key: rx.search(cmd).group(1) for key, rx in _ENV_RE.items()}
        pid = self._next_pid
        self._next_pid += 1
        self.owner[pid] = fields["run_id"]
        self.launches.append(dict(fields, pid=pid))
        # The shell redirection creates the runner log at launch time.
        self.files[f"{ROOT}/logs/{fields['run_id']}.log"] = ""
        produced = self.on_submit(fields["run_id"], fields["task"],
                                  fields["model"])
        if produced is None:
            self.alive.add(pid)
        else:
            self.files.update(produced)
        return f"{pid}\n", "", 0

    def _check(self, cmd):
        match = _RUN_ID_IN_JOBS_RE.search(cmd)
        if match is None:
            raise AssertionError(f"check command names no run id: {cmd}")
        run_id = match.group(1)
        prefix = f"{JOBS}/{run_id}/"
        if "ABSENT" not in cmd and self.crash_polls:
            self.crash_polls -= 1
            raise SimulatedCrash(f"local process died while polling {run_id}")
        results = sorted(path for path in self.files
                         if path.startswith(prefix)
                         and path.endswith("/result.json"))
        pid_alive = self._ours(cmd)
        if "ABSENT" not in cmd:  # a poll
            if results:
                return f"RESULT {results[0]}\n"
            return "PENDING\n" if pid_alive else "DEAD\n"
        # The pre-submit probe: state line, then the remote submit meta.
        meta_path = f"{JOBS}/{run_id}.ed_meta.json"
        meta_line = ""
        if meta_path in self.files:
            meta_line = "META " + self.files[meta_path].replace("\n", "") + "\n"
        if results:
            return f"RESULT {results[0]}\n" + meta_line
        run_dir_exists = (
            any(path.startswith(prefix) for path in self.files)
            or f"{ROOT}/logs/{run_id}.log" in self.files
            or meta_path in self.files)
        if not run_dir_exists:
            return "ABSENT\n"
        return ("ALIVE\n" if pid_alive else "STALE\n") + meta_line

    def _ours(self, cmd):
        """Evaluate the identity check embedded in ``cmd``."""
        match = _OURS_RE.search(cmd)
        if match is None:
            return False
        pid, run_id = int(match.group(1)), match.group(2)
        return pid in self.alive and self.owner.get(pid) == run_id
