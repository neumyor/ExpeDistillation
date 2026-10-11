"""Layer 4: functional blocks. Blocks do not import each other; composition
happens in composites/, and prompts/model calls come from the layers below."""

# Redundant-alias re-exports (no shared __all__: several agents append here,
# each exporting its own block's names).
from expedistill.blocks.audit import (
    extract_specifics as extract_specifics,
    leak_audit as leak_audit,
)
from expedistill.blocks.exam import answer as answer, judge as judge
from expedistill.blocks.metrics import (
    compute_task_metrics as compute_task_metrics,
    failed_tasks as failed_tasks,
    summarize as summarize,
)
from expedistill.blocks.rules import (
    SEGMENT_SELECT as SEGMENT_SELECT,
    TASK_SELECT as TASK_SELECT,
    TRAJECTORY_SELECT as TRAJECTORY_SELECT,
    get_segment_select as get_segment_select,
    get_task_select as get_task_select,
    get_trajectory_select as get_trajectory_select,
    register_segment_select as register_segment_select,
    register_task_select as register_task_select,
    register_trajectory_select as register_trajectory_select,
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
from expedistill.blocks.curate import (
    assign_groups as assign_groups,
    find_similar as find_similar,
    merge_skills as merge_skills,
)
