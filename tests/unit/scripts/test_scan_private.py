"""The pre-publish scanner flags deployment details and passes clean text."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "scan_private.py"
spec = importlib.util.spec_from_file_location("scan_private", SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def _hits(text, tmp_path, local=None):
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("\n".join(local or []), encoding="utf-8")
    patterns = scan.load_patterns(patterns_file)
    return [label for label, rx in patterns.items() if rx.search(text)]


def test_private_addresses_and_accounts_are_flagged(tmp_path):
    # Built at run time so this file does not itself look like a leak.
    private_ip = ".".join(["10", "20", "30", "40"])
    login = "alice@" + ".".join(["10", "0", "0", "7"])
    home = "/" + "Users" + "/alice/Code/project"

    assert _hits(f"connect to {private_ip} now", tmp_path)
    assert _hits(f"ssh -i key {login}", tmp_path)
    assert _hits(f"cd {home}", tmp_path)


def test_ordinary_text_passes(tmp_path):
    assert not _hits("use EXPEDISTILL_TB_ROOT and a placeholder <bench-root>", tmp_path)
    assert not _hits("loopback 127.0.0.1 and docs address 203.0.113.9", tmp_path)


def test_local_patterns_extend_the_builtin_ones(tmp_path):
    assert _hits("the hostname is bench42", tmp_path, local=["bench42"])
    assert not _hits("the hostname is bench42", tmp_path)


def test_comments_and_blank_lines_in_the_pattern_file_are_ignored(tmp_path):
    assert not _hits("anything", tmp_path, local=["# a comment", "", "   "])
