"""Layer 4: functional blocks. Blocks do not import each other; composition
happens in composites/, and prompts/model calls come from the layers below."""

# Redundant-alias re-exports (no shared __all__). New block names are
# registered here centrally by the integrator when a block lands -- parallel
# implementation agents never edit this file (protocol v2 integration rule).
from expedistill.blocks.metrics import (
    compute_task_metrics as compute_task_metrics,
    summarize as summarize,
)
from expedistill.blocks.rules import (
    SEGMENT_SELECT as SEGMENT_SELECT,
    TRAJECTORY_SELECT as TRAJECTORY_SELECT,
    get_segment_select as get_segment_select,
    get_trajectory_select as get_trajectory_select,
)
from expedistill.blocks.select import select_subtasks as select_subtasks
from expedistill.blocks.tasks import (
    load_tasks as load_tasks,
    split_tasks as split_tasks,
)
from expedistill.blocks.execute import (
    make_task_id as make_task_id,
    run_one as run_one,
)
from expedistill.blocks.segment import (
    segment as segment,
    validate_segments as validate_segments,
)
from expedistill.blocks.view import (
    render_steps_view as render_steps_view,
    render_trajectory_view as render_trajectory_view,
)
from expedistill.blocks.subtask_types import (
    segment_card as segment_card,
    type_segments as type_segments,
)
from expedistill.blocks.author import (
    parse_skill_fields as parse_skill_fields,
)
