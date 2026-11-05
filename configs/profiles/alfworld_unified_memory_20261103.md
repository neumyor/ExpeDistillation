# ALFWorld unified protocol: the reader's execution context

The reader executes one ALFWorld task under the benchmark's unified
protocol. At every decision step its entire context is exactly:

1. the current observation (the environment text it received just now);
2. the last two (observation, action) pairs, verbatim -- anything older has
   left the context;
3. the "Opened skill cards" block: the full text of every skill card the
   reader opened in this episode, kept verbatim until the episode ends.

Opening a card does not take one of those two interaction slots. It is not
a game action: the card text simply appears in the opened-cards block.

The reader does NOT see: the initial room description after the second
action, any interaction older than the last two, any skill card it has not
opened, a separate copy of the task instruction, or a plan it wrote in an
earlier step.

## What every written procedure must respect

- Every node in [Steps] must be executable from those three sources alone at
  the moment it fires.
- Never refer to an earlier step by position ("the object you saw two steps
  ago", "as established above", "the location from your first look"): that
  interaction is gone.
- Never assume the reader still remembers the initial room listing or the
  full instruction. A node that needs a fact must say how to re-observe it
  in that same node: look, open the receptacle, check the inventory.
- A fact stated in this card stays available while the card is open, so a
  card may carry a short list of object names, locations or inventory it
  wants the reader to keep using. It must still say how to obtain a fact
  that is neither in the card nor in the current observation.
- Prefer one self-contained action per node over a long ordered plan whose
  later nodes depend on state the reader can no longer see.
- The reader can run at most one environment action per step; a procedure
  that needs several observations must say what to check at each one.

The reader is also a weaker model: short sentences, one action per node,
explicit order, concrete observable conditions.

## Length and memory discipline when combining cards

- Keep every card short. Aim for 1,500 to 2,500 characters. A merged card
  that is longer than its sources is almost certainly repeating them.
- When several cards teach the same situation, keep the shared procedure
  and drop the repeated advice. Do not paste whole sections together.
- Never write a step that needs a fact from an earlier step, or a list the
  reader was asked to maintain. The reader has nowhere to keep it: each
  node must re-observe what it needs and must stand on its own.
- Prefer a few complete nodes over many truncated ones. If the advice does
  not fit, drop the least important advice rather than compressing every
  sentence.

## Shortening may not remove search coverage

Length is a target, coverage is a requirement. When a card says where or
how to look for something, that text stays, even if the card grows past
the target:

- Keep every search and fallback step a source card states: which places
  to check, how to check a closed container, when to move to another piece
  of furniture or room, and what to do when the first attempt fails.
- Keep every category of location the sources name. Do not narrow "surface,
  container, or place" to "receptacle or surface": an object can be on a
  bed, a sofa, a shelf, or any other named furniture. A card that searches
  only the current observation's containers is a defect.
- The prompt's admissible-action list names every place the reader can move
  to right now (furniture, containers, and any other place the environment
  lists), and it stays visible at every step. The initial room description
  does not. A search step must tell the reader to choose the next place
  from the admissible-action list, not only from the current observation:
  after two steps the bed, the drawers, and the rest are no longer in the
  observation, but they are still in that list.
- Keep every action command the sources use (open, examine, go to, take,
  use, move) and the exact command shape. A paraphrase such as "look at the
  receptacles" is not an executable command; write "go to X, open X,
  examine X".
- Shorten by removing repeated wording and redundant explanations, never by
  deleting a step, a location category, or an action.
