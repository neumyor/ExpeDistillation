"""Exam prompts: G3 scene/trigger/plan generation and G5 judging.

The exam is a TEXT exam assembled from one real trajectory (the generalization
exam, docs/decisions/generalization_exam_20261027.md). The scene replays a real
prefix and describes the step the trajectory actually took next; the examined
model then has to find the relevant skill in the whole library catalog and
write a plan; a judge decides whether that plan handles the scene.

The scene is written BEFORE the model reads the catalog, so EXAM_SCENE must not
name the skill or the catalog: a scene that mentions the skill under
examination hands the reader the answer to the trigger step.

The judge is a closed-vocabulary verdict prompt (success/failure). Its reason
is evidence for the review/rewrite path, so it must say WHAT is wrong, never how
to fix it -- the same independence rule the retired JUDGE prompt carried.
"""

from expedistill.prompts.registry import PromptSpec, register

__all__ = ["EXAM_SCENE", "EXAM_TRIGGER", "EXAM_PLAN", "EXAM_JUDGE"]

EXAM_SCENE = register(
    PromptSpec(
        name="EXAM_SCENE",
        template="""Reconstruct one exam scene from a real trajectory. The scene is the
situation a reader faces when it reaches one cut point of the trajectory; the
reader will have to decide what to do next without seeing the steps below or
the skill that was written from them.

TASK INSTRUCTION (what the agent that produced the trajectory was asked to do):
{task_instruction}

STEPS BEFORE THE CUT (the real prefix; the reader sees everything up to here):
{prior_steps}

STEPS AFTER THE CUT (what actually happened next; ground the expected behavior
in these):
{future_steps}

THE SKILL UNDER EXAMINATION (its full text; use it only to understand what a
correct reader must notice and do):
{skill_content}

WHY THIS SKILL WAS WRITTEN OR CHANGED:
{change_note}

Write three fields, all in natural language:

- scene_text: the situation at the cut point, self-contained. Describe the
  task, the state reached so far, and the observations that matter, so a reader
  who never saw the trajectory can decide what to do next. End at the cut:
  never include the action or the observations that come after it.
- expected_behavior: what a reader who understands the situation should do
  next, and what it should verify. Say the behavior generally; it is the
  grading standard for a plan, not this trajectory's command lines.
- reference_answer: the best answer to the scene: the right action, in what
  order, and why. This is a grading reference and is never shown to the
  examined reader.

Hard rules:
- scene_text must NOT name or allude to any skill, the skill catalog, a skill
  file, or the fact that this scene comes from an exam or a trajectory.
- Use natural language throughout: no step indices, no ids, no bookkeeping
  fields. Concrete observations and values from the trajectory are allowed.
- Do not ask the reader a question; state the situation.

Output ONLY JSON with exactly these fields:
{{"scene_text": "<the self-contained situation at the cut point>",
  "expected_behavior": "<what a correct reader should do next and verify>",
  "reference_answer": "<the right action, order, and why>"}}
""",
        description=(
            "G3 exam scene: reconstruct a self-contained scene and its "
            "expected behavior from a real trajectory."
        ),
    )
)

EXAM_TRIGGER = register(
    PromptSpec(
        name="EXAM_TRIGGER",
        template="""Choose which skills from the library catalog apply to the situation below.
The reader may open at most {max_skill_reads} skill(s); every opened skill must
be justified by what its catalog line says.

SCENE:
{scene_text}

SKILL CATALOG (one entry per skill; the name is what you return):
{skill_catalog}

Rules:
- Open a skill only when its description or trigger says it applies to THIS
  scene. Do not open a skill because it might be useful in general.
- Return an empty list when no skill applies; that is a valid answer.
- Use each name exactly as the catalog writes it. Never invent a name.
- Do not return more than {max_skill_reads} skill(s).

Output ONLY JSON with exactly these fields:
{{"skills": ["<skill name>", ...],
  "reason": "<one or two sentences: why these skills apply, or why none>"}}
""",
        description=(
            "G3 exam trigger: pick at most max_skill_reads skills from the "
            "library catalog for a scene."
        ),
    )
)

EXAM_PLAN = register(
    PromptSpec(
        name="EXAM_PLAN",
        template="""Write an action plan for the situation below, using the skills that were
opened for it.

SCENE:
{scene_text}

WHY THESE SKILLS WERE OPENED:
{selection_reason}

OPENED SKILLS:
{selected_skills}

Write a plan the reader would follow now:
- say what to do first, then the following steps, in order;
- follow the opened skills where they apply; if none applies, plan from your
  own knowledge;
- name what to check after each step that changes the state;
- stop at what can be decided from the scene: do not invent facts the scene
  does not give.

Output ONLY JSON with exactly these fields:
{{"plan": "<the ordered plan as one string, or an ordered array of step strings>"}}
""",
        description=(
            "G4 exam plan: write an ordered plan for a scene from the opened "
            "skills."
        ),
    )
)

EXAM_JUDGE = register(
    PromptSpec(
        name="EXAM_JUDGE",
        template="""Judge whether the plan below handles the scene correctly.

SCENE:
{scene_text}

EXPECTED BEHAVIOR:
{expected_behavior}

REFERENCE ANSWER (grading standard; never shown to the examined model):
{reference_answer}

SKILL CATALOG (the library the model chose from):
{skill_catalog}

SKILLS THE MODEL OPENED:
{selected_skills}

WHY IT OPENED THEM:
{selection_reason}

THE MODEL'S PLAN:
{plan}

The verdict is "success" when the plan would handle the scene: it takes the
right actions in a workable order, follows the opened skills where they apply,
and verifies what needs verifying. Wording may differ from the reference
answer; judge the behavior, not the phrasing. Missing a needed skill when the
plan still recovers correctly is not a failure by itself. The verdict is
"failure" otherwise.

The reason must say only WHAT is wrong with the plan: name the defect (a wrong
action, a missing step, a misread observation, an unverified result). Do not
state or imply how to fix it: never describe the correct approach, a step to
add or remove, or what the plan should have said instead.

Output ONLY JSON with exactly these fields:
{{"verdict": "<success|failure>",
  "reason": "<one to three sentences: why the plan succeeds, or only what is
  wrong with it>"}}
""",
        description=(
            "G5 exam judging: success/failure verdict for a plan against the "
            "scene, never revealing the fix."
        ),
    )
)
