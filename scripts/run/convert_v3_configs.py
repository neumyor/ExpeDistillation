"""Convert schema-v3 experiment configs to v4 (one-time, the phase world).

Usage:
    .venv/bin/python scripts/run/convert_v3_configs.py [--dir configs/experiments] [files...]

Input: explicit file paths, or (the default) every schema_version: 3
config in --dir -- the seven frozen v3 experiment records. A v2 (or
pre-schema) file may be passed EXPLICITLY; it chains upgrade_v2_config ->
upgrade_v3_config in memory. A v2 file whose <stem>_v3.yaml already
exists is refused with a pointer at that v3 record: the one-time v2 -> v3
conversion (scripts/run/convert_v2_configs.py) already wrote the file of
record, and converting the v2 file again would freeze a second identity
for the same experiment.

Output: <stem>_v4.yaml next to the input (NEVER overwritten; v3 files
are frozen experiment records, AGENTS section 6). The write is atomic
(temp file + rename, the Wave 3 review O4 ruling): an interrupted run
leaves the previous state, never a half-written *_v4.yaml that the
never-overwrite rule would then lock in. The mapping is
core.config.upgrade_v3_config -- the same pure function the reuse
assembler applies to a v3 source run's manifest.

Every written file is verified by loading it back through load_config,
which runs the full v4 load-time checklist (the phase sequence, the
role/field consistency, the compare targets). The per-file summary
prints the emitted sequence and the disclosed identity changes (the
acceptance slot dissolving into the sequence, live_run's params moving
into the live phase, merge_check following the acceptance kind).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import yaml  # noqa: E402

from expedistill.core.config import (  # noqa: E402
    CONFIG_SCHEMA_VERSION,
    ExperimentConfig,
    load_config,
    save_config,
    upgrade_v2_config,
    upgrade_v3_config,
)



def detect_version(raw: dict) -> int:
    """The config's schema version: the explicit ``schema_version`` key,
    else 3 when a ``methods`` mapping is present, else 2 (a pre-schema
    file is v2-shaped for the chain -- the same default the v2
    converter applied)."""
    version = raw.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        return version
    return 3 if isinstance(raw.get("methods"), dict) else 2


def convert_file(path: Path, *, out_dir=None) -> str:
    """Convert one config file; returns a one-line status for the report.

    ``out_dir`` redirects the output (the tests write into a temporary
    directory so the repository's configs stay untouched).
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        # The reader most likely to misplace a flag or a path is the
        # one who least needs a traceback (Wave 3 review D7): name the
        # file and the reason, per file like every other refusal.
        return f"{path.name}: REFUSED (cannot read: {exc})"
    except yaml.YAMLError as exc:
        return f"{path.name}: REFUSED (not parsable YAML: {str(exc).splitlines()[0]})"
    if not isinstance(raw, dict):
        return f"{path.name}: REFUSED (not a YAML mapping)"
    version = detect_version(raw)
    if version >= CONFIG_SCHEMA_VERSION:
        return f"{path.name}: already schema v{version}, skipped"
    if version < 3:
        v3_record = path.with_name(path.stem + "_v3.yaml")
        if v3_record.exists():
            return (f"{path.name}: REFUSED (its v3 record "
                    f"{v3_record.name} already exists; convert THAT file "
                    "instead)")
    # The upgrade and the validation are one unit: an input the mapping
    # refuses (a v3 key with no v4 destination, an illegal method
    # combination) or that fails the v4 load-time checklist is refused
    # per file, never a converter bug.
    try:
        upgraded = (upgrade_v3_config(upgrade_v2_config(raw))
                    if version < 3 else upgrade_v3_config(raw))
        # from_dict runs the whole v4 load-time checklist (parse_phases
        # included) BEFORE anything is written.
        config = ExperimentConfig.from_dict(upgraded)
    except (ValueError, TypeError) as exc:
        return f"{path.name}: REFUSED ({exc})"
    out = (Path(out_dir) if out_dir is not None
           else path.parent) / f"{path.stem}_v4.yaml"
    if out.exists():
        return f"{out.name}: exists, left untouched"
    # Atomic write (O4): a temp file in the destination directory, then
    # rename. A crash mid-write leaves no half *_v4.yaml behind -- the
    # never-overwrite rule would otherwise lock a truncated file in and
    # silently block the retry. The verification loads the TEMP file, so
    # only a file today's code accepts is ever renamed into place.
    fd, temp_name = tempfile.mkstemp(dir=str(out.parent),
                                     prefix=out.name + ".",
                                     suffix=".tmp")
    os.close(fd)
    temp = Path(temp_name)
    try:
        save_config(config, str(temp))
        load_config(str(temp))
        os.replace(temp, out)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    acceptance = ((raw.get("methods") or {}).get("acceptance") or {})
    return (f"{path.name} -> {out.name} (sequence: "
            f"{' '.join(config.sequence)}; acceptance slot "
            f"{acceptance.get('name', 'exam')!r} dissolved into it)")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = None
    positional = []
    # Hand-rolled parsing with actionable usage errors (Wave 3 review
    # D7): a "--dir" without a value used to die on an IndexError, and a
    # mistyped flag was silently treated as a file path (its traceback
    # pointed at open(), not at the typo). Both now print one line that
    # says what to do instead.
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--dir":
            if index + 1 >= len(argv):
                print("usage: convert_v3_configs.py [--dir <directory of "
                      "v3 configs>] [config files...]\n"
                      f"error: {arg} needs a value")
                return 2
            directory = Path(argv[index + 1])
            index += 2
            continue
        if arg.startswith("--"):
            print(f"error: unknown flag {arg!r}; the only flag is "
                  "--dir <directory>; positional arguments are config "
                  "file paths")
            return 2
        positional.append(arg)
        index += 1
    if directory is None:
        directory = (Path(__file__).resolve().parents[2]
                     / "configs" / "experiments")
    explicit = [Path(arg) for arg in positional]
    if explicit:
        paths = explicit
    else:
        # The default scan converts exactly the schema-version-3 files:
        # they are the frozen records of record, and a v1/v2 file's v4
        # identity goes through its v3 image (pass such a file
        # explicitly when no v3 image exists).
        paths = []
        for path in sorted(directory.glob("*.yaml")):
            if path.stem.endswith("_v4"):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                version = detect_version(raw) if isinstance(raw, dict) \
                    else None
            except (OSError, yaml.YAMLError):
                version = None
            if version == 3:
                paths.append(path)
    for path in paths:
        print(convert_file(path))
    print(f"converted {len(paths)} config(s); review each *_v4.yaml "
          "before launching anything from it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
