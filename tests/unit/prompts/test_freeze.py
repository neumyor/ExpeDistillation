"""The two static tests that make the v3 full freeze sound (plan section 5).

The manifest freezes the digests of every REGISTERED method prompt plus
the run's task prompt. That is only complete because nothing can register
outside the two homes these tests pin:

1. every ``prompts/*.py`` module is imported by ``prompts/__init__`` (its
   import list IS the frozen name set -- a prompt module outside the list
   would register only when something else happens to import it, and its
   name would drift in and out of manifests);
2. ``register(PromptSpec(...))`` appears only in ``prompts/`` and in
   ``benchmarks/<name>/`` -- and each benchmark package registers at most
   ONE prompt, its own task prompt (a second registration would either
   miss the freeze entirely or leak a benchmark-private prompt into
   every manifest of that benchmark).
"""

import ast
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[3] / "src" / "expedistill")
PROMPTS = ROOT / "prompts"


def test_every_prompt_module_is_imported_by_the_package():
    imported = set()
    tree = ast.parse((PROMPTS / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "expedistill.prompts":
            imported.update(alias.name for alias in node.names)
    modules = {path.stem for path in PROMPTS.glob("*.py")
               if path.stem not in ("__init__", "registry")}
    missing = sorted(modules - imported)
    assert not missing, (
        f"prompt module(s) outside prompts/__init__'s import list: "
        f"{missing}; a prompt outside the list registers only when "
        "something else imports it, so the frozen name set would drift")


def test_prompts_register_only_in_their_two_homes():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        parts = rel.parts
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "register"):
                if parts[0] == "prompts":
                    continue
                if parts[0] == "benchmarks":
                    # Any registration inside a benchmark package is
                    # structurally home; the count test below polices how
                    # many (one task prompt per benchmark).
                    continue
                offenders.append(str(rel))
    assert not offenders, (
        "prompt registration outside prompts/ and benchmarks/<name>/: "
        f"{offenders}")


def test_each_benchmark_registers_at_most_its_declared_prompts():
    for package in sorted((ROOT / "benchmarks").iterdir()):
        if not package.is_dir():
            continue
        count = 0
        declares_folder = False
        for path in package.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            if "skill_folder_prompt_name" in source:
                declares_folder = True
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "register"):
                    count += 1
        # One benchmark = one task prompt; the extra registrations a
        # plugin may carry are the skill-folder capability's prompt
        # (skill_folder_prompt_name, skill-folder M3) and its inline-
        # catalog companion (the same name + "_INLINE", which stage 0
        # freezes by that convention when it is registered) -- both
        # freeze with the task prompt, so they neither miss the freeze
        # nor leak. Anything else would.
        cap = 3 if declares_folder else 1
        assert count <= cap, (
            f"benchmark package {package.name} registers {count} prompts; "
            "one benchmark = one task prompt plus at most the declared "
            "skill-folder prompt (extra registrations would either miss "
            "the freeze or leak benchmark-private prompts)")
