"""Layer 4: functional blocks. Blocks do not import each other; composition
happens in composites/, and prompts/model calls come from the layers below."""

# Redundant-alias re-exports (no shared __all__). New block names are
# registered here centrally by the integrator when a block lands -- parallel
# implementation agents never edit this file (protocol v2 integration rule).
from expedistill.blocks.attribution import (
    attribute_attempts as attribute_attempts,
    revise_from_attempts as revise_from_attempts,
)
from expedistill.blocks.audit import (
    audit_leak as audit_leak,
)
from expedistill.blocks.exam import answer as answer, judge as judge
from expedistill.blocks.metrics import (
    compute_task_metrics as compute_task_metrics,
    failed_tasks as failed_tasks,
    summarize as summarize,
)
from expedistill.blocks.rules import (
    ACCEPTANCE_IMPROVE as ACCEPTANCE_IMPROVE,
    SEGMENT_SELECT as SEGMENT_SELECT,
    TASK_SELECT as TASK_SELECT,
    TRAJECTORY_SELECT as TRAJECTORY_SELECT,
    get_acceptance_improve as get_acceptance_improve,
    get_segment_select as get_segment_select,
    get_task_select as get_task_select,
    get_trajectory_select as get_trajectory_select,
    register_acceptance_improve as register_acceptance_improve,
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
from expedistill.blocks.author import (
    has_skill_sections as has_skill_sections,
    render_skill_content as render_skill_content,
    validate_skill_sections as validate_skill_sections,
)
from expedistill.blocks.retrieve import (
    derive_categories as derive_categories,
    select_skills as select_skills,
)
