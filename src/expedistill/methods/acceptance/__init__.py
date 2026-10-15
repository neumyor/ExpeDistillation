"""The stage-4 acceptance drivers' shared layout constants.

The stage directory and the summary file name are shared by both
drivers (and read by the audit tooling). The failure-ledger label is
NOT here: each driver writes ``failed_units.jsonl`` rows under its own
stage identity (exam: ``stage4_acceptance``, live: ``stage4_live``), so
the labels live with their drivers.
"""

__all__ = ["STAGE4_DIR", "SUMMARY_FILENAME"]

#: The stage's directory in the run layout (README section 2).
STAGE4_DIR = "stage4_acceptance"

#: Name of the stage summary inside ``STAGE4_DIR``.
SUMMARY_FILENAME = "stage4_summary.json"
