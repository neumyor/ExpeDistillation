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


def _module_constants(tree):
    """Module-level ``NAME = "literal"`` assignments in one file."""
    values = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            values[node.targets[0].id] = node.value.value
    return values


def _prompt_spec_name(call, constants):
    """The ``name`` of one PromptSpec(...) call, or None when unresolved."""
    spec = next((keyword.value for keyword in call.keywords
                 if keyword.arg == "name"), None)
    if isinstance(spec, ast.Constant) and isinstance(spec.value, str):
        return spec.value
    if isinstance(spec, ast.Name):
        return constants.get(spec.id)
    return None


def _registered_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    # Some plugins name a PromptSpec at module level and register the
    # variable (searchqa); others call register(PromptSpec(...)) inline.
    specs = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "PromptSpec"):
            name = _prompt_spec_name(node.value, constants)
            if name is not None:
                specs[node.targets[0].id] = name
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "register"):
            continue
        argument = node.args[0] if node.args else None
        name = None
        if isinstance(argument, ast.Call):
            name = _prompt_spec_name(argument, constants)
        elif isinstance(argument, ast.Name):
            name = specs.get(argument.id)
        if name is None:
            name = next((keyword.value.value for keyword in node.keywords
                         if keyword.arg == "name"
                         and isinstance(keyword.value, ast.Constant)), None)
        if name is None:
            raise AssertionError(
                f"{path}: cannot resolve the registered prompt name")
        names.add(name)
    return names


def test_each_benchmark_registers_only_prompts_its_plugin_declares():
    """Every prompt a benchmark registers must be in its frozen set.

    A plugin declares the prompts it runs under: ``task_prompt_name``, the
    optional ``skill_folder_prompt_name``, ``protocol_prompt_names`` and the
    optional ``playbook_prompt_name``.
    Stage 0 freezes exactly those. A registration outside the declared set
    would either miss the freeze or leak a benchmark-private prompt into
    every manifest of that benchmark.
    """
    from tests.helpers.plugins import ensure_plugin

    for package in sorted((ROOT / "benchmarks").iterdir()):
        if not package.is_dir() or not (package / "__init__.py").exists():
            continue
        plugin = ensure_plugin(package.name)
        declared = {plugin.task_prompt_name}
        if plugin.skill_folder_prompt_name is not None:
            declared.add(plugin.skill_folder_prompt_name)
        declared.update(plugin.protocol_prompt_names)
        if plugin.playbook_prompt_name is not None:
            declared.add(plugin.playbook_prompt_name)
        registered = set()
        for path in package.glob("*.py"):
            registered |= _registered_names(path)
        assert registered <= declared, (
            f"benchmark package {package.name} registers undeclared "
            f"prompt(s) {sorted(registered - declared)}; declare them on "
            "the plugin (task/folder/protocol names) so stage 0 freezes "
            "them")
