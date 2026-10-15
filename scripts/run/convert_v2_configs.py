"""Convert schema-v2 experiment configs to v3 (one-time, the P1b switch).

Usage:
    .venv/bin/python scripts/run/convert_v2_configs.py [--dir configs/experiments]

For every v2 config in the directory, writes <stem>_v3.yaml (NEVER
overwrites: the v2 files are frozen experiment records, AGENTS section 6)
and prints a per-file summary of what the upgrade moved or dropped, for
the human review the plan's P1b row requires. The mapping itself is
core.config.upgrade_v2_config -- the same pure function the reuse
assembler applies to v2 source manifests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import yaml  # noqa: E402

from expedistill.core.config import (  # noqa: E402
    CONFIG_SCHEMA_VERSION,
    ExperimentConfig,
    save_config,
    upgrade_v2_config,
)

#: v2 keys the upgrade relocates (or drops, for live's retry_limit); the
#: summary prints each so the human review sees exactly what moved.
MOVED_KEYS = ("K", "segment_retry_limit", "retry_limit",
              "bank_token_budget", "curation_rounds",
              "skill_delivery", "acceptance")


def main() -> int:
    directory = (Path(sys.argv[sys.argv.index("--dir") + 1])
                 if "--dir" in sys.argv
                 else Path(__file__).resolve().parents[2]
                 / "configs" / "experiments")
    converted = 0
    for path in sorted(directory.glob("*.yaml")):
        if path.stem.endswith("_v3"):
            continue
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        version = raw.get("schema_version", 2)
        if version >= CONFIG_SCHEMA_VERSION:
            print(f"{path.name}: already schema v{version}, skipped")
            continue
        upgraded = upgrade_v2_config(raw)
        # Validate by constructing; a config that was already invalid
        # UNDER V2's OWN RULES (e.g. the pre-schema pilots' model_options
        # max_tokens) is refused per file, not a converter bug.
        try:
            ExperimentConfig.from_dict(upgraded)
        except (ValueError, TypeError) as exc:
            print(f"{path.name}: REFUSED ({exc}); fix or archive the v2 "
                  "file by hand")
            continue
        out = path.with_name(path.stem + "_v3.yaml")
        if out.exists():
            print(f"{out.name}: exists, left untouched")
            continue
        save_config(ExperimentConfig.from_dict(upgraded), str(out))
        moved = [key for key in MOVED_KEYS if key in raw]
        print(f"{path.name} -> {out.name} (moved: {', '.join(moved) or '-'})")
        converted += 1
    print(f"converted {converted} config(s); review each *_v3.yaml "
          "before launching anything from it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
