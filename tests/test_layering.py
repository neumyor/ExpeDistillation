"""Package layers may import downward only, including function-local imports.

Adapted from the SkillExpand repo's tests/test_layering.py (ExpeToSkill-merged
@ TB-eval 4f436af); same two checks, this package's layer table (AGENTS.md
section 4): core=0; infra/prompts/inject=1; llm=2; benchmarks=3; blocks=4;
composites=5; methods=6; stages=7; cli=8; __main__=9.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'expedistill'

#: Lower numbers are lower layers. Layer-1 packages share rank and may not
#: import each other (checked by review; same-rank imports are allowed here).
RANK = {
    'core': 0,
    'infra': 1, 'prompts': 1, 'inject': 1,
    'llm': 2,
    'benchmarks': 3,
    'blocks': 4,
    'composites': 5,
    'methods': 6,
    'stages': 7,
    'cli': 8,
    '__main__': 9,
}

#: Method drivers may import their own slot package and everything below,
#: never another slot's or method's module (the multi-method plan section 2:
#: a method's seams live in the registry, not in each other). The package
#: __init__ files are the explicit registration/exports and are exempt.
_METHODS_ROOT = 'expedistill.methods'


def _imports(path):
    package = '.'.join(path.relative_to(ROOT).with_suffix('').parts[:-1])
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                # A relative import names the same package tree; resolve it
                # so the rank check covers it too (it used to be skipped,
                # which hid same-rank and upward imports from the check).
                base = package.rsplit('.', node.level - 1)[0] \
                    if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module
            yield node.lineno, module
            if module == 'expedistill':
                for alias in node.names:
                    yield node.lineno, f'expedistill.{alias.name}'
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def test_every_module_belongs_to_a_layer():
    layers = {p.relative_to(ROOT).with_suffix('').parts[0] for p in ROOT.rglob('*.py')}
    assert layers - {'__init__'} <= set(RANK)


def test_no_upward_imports():
    upward = []
    for path in sorted(ROOT.rglob('*.py')):
        source = path.relative_to(ROOT).with_suffix('').parts[0]
        if source == '__init__':
            continue
        for line, module in _imports(path):
            parts = module.split('.')
            if parts[0] != 'expedistill' or len(parts) < 2 or parts[1] not in RANK:
                continue
            if RANK[parts[1]] > RANK[source]:
                upward.append(f'{path.relative_to(ROOT)}:{line} imports {module}')
    assert not upward, '\n'.join(upward)


def test_methods_do_not_import_each_other():
    """One method driver never imports another slot's or method's module.

    The registry is the only seam between methods (the plan's section 2:
    ``methods/acceptance/exam`` may not import ``live_run`` or anything
    under ``methods/delivery``, and vice versa); a violation here is a
    method reaching around the by-name dispatch.
    """
    offenders = []
    for path in sorted((ROOT / 'methods').rglob('*.py')):
        if path.name == '__init__.py':
            continue
        own = 'expedistill.' + '.'.join(
            path.relative_to(ROOT).with_suffix('').parts[:-1])
        for line, module in _imports(path):
            if module == _METHODS_ROOT or not module.startswith(_METHODS_ROOT + '.'):
                continue
            if not (module == own or module.startswith(own + '.')):
                offenders.append(
                    f'{path.relative_to(ROOT)}:{line} imports {module}')
    assert not offenders, '\n'.join(offenders)
