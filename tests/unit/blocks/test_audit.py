"""D8 leak audit unit tests: specificity extraction and threshold behaviour.

Pure functions, so every test is direct computation; the deterministic claim
is asserted explicitly (same inputs, same outputs across calls)."""

from expedistill.blocks.audit import extract_specifics, leak_audit

# Four specifics: the quoted span, the IP, the port number, and the acronym.
QUESTION = (
    'The log shows "connection refused" from 10.0.0.7 while starting the '
    "AUTH service on port 8443."
)
REFERENCE = "Confirm port 8443 is open and the AUTH service config is valid."
CLEAN_SKILL = "Check the firewall rules, then retry the connection."


def test_extracts_quoted_spans():
    s = extract_specifics('The log says "connection refused" and \'restart failed\'.', "")
    assert "connection refused" in s
    assert "restart failed" in s


def test_word_internal_apostrophe_is_not_a_quote():
    s = extract_specifics("It doesn't restart the 'auth daemon' here.", "")
    assert "auth daemon" in s
    assert not any("t restart" in item for item in s)


def test_extracts_numbers_including_versions():
    s = extract_specifics(
        "Retry after 30 seconds, timeout 1.5 seconds, version 1.2.3, not v2.", ""
    )
    assert "30" in s
    assert "1.5" in s
    assert "1.2.3" in s
    assert "2" not in s  # digits glued to letters are not bare numbers
    assert "v2" not in s


def test_extracts_file_name_like_tokens():
    s = extract_specifics(
        "Read /etc/nginx.conf and run_all.sh, then check server.log.", ""
    )
    assert "/etc/nginx.conf" in s
    assert "run_all.sh" in s
    assert "server.log" in s


def test_extracts_all_caps_acronyms():
    s = extract_specifics("Restart the AUTH service and check GPU usage.", "")
    assert "auth" in s
    assert "gpu" in s


def test_specifics_are_case_normalized():
    s = extract_specifics("Run cleanup.SH and check the LOG file on host DB-01.", "")
    assert s == {item.lower() for item in s}
    assert "cleanup.sh" in s


def test_no_specifics_passes_with_empty_report():
    ok, report = leak_audit("Anything at all.", "What should you do next?", "Do it.")
    assert ok is True
    assert report == ""


def test_no_hits_passes_with_empty_report():
    ok, report = leak_audit(CLEAN_SKILL, QUESTION, REFERENCE)
    assert ok is True
    assert report == ""


def test_ratio_exactly_at_threshold_passes():
    question = 'Update the "app.conf" file on port 8000.'
    skill = "Before restarting, update the app.conf file."
    # One of two specifics hit: ratio 0.5 == default threshold -> pass.
    ok, report = leak_audit(skill, question, "No extras.")
    assert ok is True
    assert "1/2" in report
    assert "app.conf" in report
    # Just below the same ratio -> fail.
    ok_tighter, _ = leak_audit(skill, question, "No extras.", threshold=0.49)
    assert ok_tighter is False


def test_threshold_zero_fails_on_any_hit():
    question = 'Update the "app.conf" file on port 8000.'
    ok, _ = leak_audit("Update app.conf.", question, "No extras.", threshold=0.0)
    assert ok is False


def test_failing_report_lists_all_hits():
    question = 'Update the "app.conf" file on port 8000.'
    skill = "Edit app.conf and set port 8000."
    ok, report = leak_audit(skill, question, "No extras.")
    assert ok is False
    assert "2/2" in report
    assert "app.conf" in report
    assert "8000" in report


def test_hit_matching_is_case_insensitive():
    question = 'Update the "app.conf" file on port 8000.'
    ok, report = leak_audit("Edit APP.CONF now.", question, "No extras.")
    # Ratio 0.5 == threshold, so it passes, but the hit is still reported.
    assert ok is True
    assert "app.conf" in report


def test_audit_is_deterministic_across_calls():
    first = leak_audit("Edit app.conf and set port 8000.", QUESTION, REFERENCE)
    second = leak_audit("Edit app.conf and set port 8000.", QUESTION, REFERENCE)
    assert first == second
    assert extract_specifics(QUESTION, REFERENCE) == extract_specifics(
        QUESTION, REFERENCE
    )
