"""Package layers may import downward only, including function-local imports.

Adapted from the SkillExpand repo's tests/test_layering.py (ExpeToSkill-merged
@ TB-eval 4f436af); same two checks, this package's layer table (AGENTS.md
section 4): core=0; infra/prompts/inject=1; llm=2; benchmarks=3; blocks=4;
composites=5; stages=6; cli=7; __main__=8.
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
    'stages': 6,
    'cli': 7,
    '__main__': 8,
}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.lineno, node.module
            if node.module == 'expedistill':
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
