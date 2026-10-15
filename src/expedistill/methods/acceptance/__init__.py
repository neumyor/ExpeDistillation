"""The stage-4 acceptance drivers' shared layout constants.

The stage directory and the summary file name are shared by both
drivers (and read by the audit tooling); the failure-ledger label is
shared because both drivers write ``failed_units.jsonl`` rows under the
same stage identity. Living here (not in stages/) is what lets the
drivers read them without importing upward.
"""

__all__ = ["STAGE4_DIR", "STAGE_LABEL", "SUMMARY_FILENAME"]

#: The stage's directory in the run layout (README section 2).
STAGE4_DIR = "stage4_acceptance"

#: Name of the stage summary inside ``STAGE4_DIR``.
SUMMARY_FILENAME = "stage4_summary.json"

#: Stage label of this stage's lines in the unit-failure ledger.
STAGE_LABEL = "stage4_acceptance"
