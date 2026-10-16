"""Contract tests for the M3 registry families (evidence / author).

The two content families' entries carry params only (plan section 9's
M3 row: the config wiring waits for M4), so the contract to pin is the
resolution and the params' isomorphism with the slot entries they were
re-homed from. ``resolve_fixed`` itself cannot drive these yet -- it
loads the driver, and these entries are driver-less until the phase
drivers exist -- so the tests resolve through the same table and the
same ``build_params`` validation, by the family's fixed name over a
bare methods mapping (the ``resolve_fixed`` form, without the config).
"""

from types import SimpleNamespace

import pytest

from expedistill.core.config import ConfigError
from expedistill.methods import resolve_params
from expedistill.methods.author import AuthoringParams
from expedistill.methods.authoring import AuthoringParams as SlotAuthoringParams
from expedistill.methods.evidence import SegmentationParams
from expedistill.methods.segmentation import (
    SegmentationParams as SlotSegmentationParams,
)


def _config(methods):
    return SimpleNamespace(methods=methods)


def test_evidence_segment_params_match_the_segmentation_slot():
    # Same class, same validation, same values: the family entry resolves
    # the re-homed params exactly as the v3 slot entry does.
    config = _config({"evidence": {"K": 5, "retry_limit": 2},
                      "segmentation": {"K": 5, "retry_limit": 2}})
    assert resolve_params("evidence", config) == \
        resolve_params("segmentation", config) == \
        SegmentationParams(K=5, retry_limit=2)


def test_evidence_completes_its_default_retry_limit():
    params = resolve_params("evidence", _config({"evidence": {"K": 3}}))
    assert params == SegmentationParams(K=3, retry_limit=2)


def test_author_params_match_the_authoring_slot():
    # The exams switch stays (M4 removes it with the call-point move); the
    # v3 slot's cross-slot check runs on the slot entry only.
    config = _config({"author": {}, "authoring": {}})
    assert resolve_params("author", config) == \
        resolve_params("authoring", config) == AuthoringParams(exams=True)


def test_rehomed_params_are_one_class_not_a_copy():
    # The slot packages re-export the family packages' classes.
    assert SlotSegmentationParams is SegmentationParams
    assert SlotAuthoringParams is AuthoringParams


def test_unknown_name_lists_the_known_ones():
    with pytest.raises(ConfigError, match=r"known: \['segment'\]"):
        resolve_params("evidence", _config({"evidence": {"name": "windows",
                                                         "K": 5}}))
    with pytest.raises(ConfigError, match=r"known: \['distill_diagnose'\]"):
        resolve_params("author", _config({"author": {"name": "solo"}}))


def test_required_params_still_fail_loudly():
    # The fixed-name form validates too: an entry with required fields
    # cannot be resolved without them.
    with pytest.raises(ConfigError, match="missing required parameter"):
        resolve_params("evidence", _config({"evidence": {}}))
