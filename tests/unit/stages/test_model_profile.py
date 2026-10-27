"""The accumulated model profile artifact."""

from expedistill.core.config import LearnPhaseSpec
from expedistill.stages import model_profile


def test_empty_initial_profile_and_next_phase_read(tmp_path):
    names = ["baseline", "learn", "learn"]
    specs = {
        "baseline": object(),
        "learn": LearnPhaseSpec(review={"name": "review"},
                                author={"name": "distill_diagnose"},
                                curate={"name": "none"}),
    }
    text, version = model_profile.load_previous_profile(
        tmp_path, names, specs, 2)
    assert (text, version) == ("", 0)

    phase_dir = tmp_path / "phases" / "p02_learn"
    model_profile.write_profile(
        phase_dir, "p02:learn", "the model ignores verification", ["s1"], 1)
    text, version = model_profile.load_previous_profile(
        tmp_path, names, specs, 2)
    assert text == "the model ignores verification"
    assert version == 1
