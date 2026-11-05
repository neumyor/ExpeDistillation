# ALFWorld global playbook

Always answer `<think>brief reason</think><action>one admissible action</action>`;
choose only from the admissible action list.

## Task recipes

| Type | Recipe |
|---|---|
| Pick & Place | find X -> take X -> go to Y -> put X in/on Y |
| Pick Two & Place | choose ONE destination Y; find X1 -> take -> put in Y; find X2 -> take -> put in the SAME Y |
| Examine in Light | find X -> take X -> go where a desklamp is visible -> use desklamp; do not put X on the lamp |
| Clean & Place | find X -> take X -> go to sinkbasin -> clean X with sinkbasin -> go to Y -> put X |
| Heat & Place | find X -> take X -> go to microwave -> heat X with microwave -> go to Y -> put X |
| Cool & Place | find X -> take X -> go to fridge -> cool X with fridge -> go to Y -> put X |

Clean is exactly these five steps: no extra inventory checks, no
re-verification after cleaning. Pick Two: choose one usable destination
instance once, put both objects there, do not remove the first, and return
to a remembered second source instead of restarting a room search.

## General principles

1. Decompose: locate -> acquire -> transform (if needed) -> deliver.
2. Search each surface/container exactly once; open closed containers
   before judging them empty.
3. Use priors, then broaden: food in fridge/countertop/dining table;
   dishes/cookware on countertop/dining table/stoveburner/cabinet/drawer;
   office items on desk/shelf/dresser/sidetable/drawer; newspapers on
   coffeetable/sidetable/sofa/tvstand; cleaning items near sink/shelf/cart.
4. Grab immediately when the exact requested object is visible. Only the
   exact object counts (a cup is not a mug, a spoon is not a knife, a pot
   is not a pan); mark that place searched and move on.
5. Transform before delivery. Remember the appliance and destination but
   do not revisit them while empty-handed.
6. Once holding the transformed object, go straight to the remembered
   destination and place it.
7. Never repeat the same action more than twice; if stuck, move to a
   different unvisited place.

## Hard search-loop recovery

- **Exact-instance lockout**: once an exact instance has been observed and
  lacks the target, never return to it while empty-handed. Only holding
  the object or being ready to deliver justifies returning.
- **Broaden after 3-4 misses** in one class: switch to another class or
  any unvisited admissible place; never restart the class.
- **No reset by recency**: a place is not new because it left the last two
  observations. The searched set is global for the episode.
- **Finite-class exhaustion**: after every visible instance of a small
  class (stoveburners, dining tables, countertops, shelves) has been
  checked once, declare the class exhausted; no second pass.
- **Unvisited beats likely-but-searched**: after several misses, prefer
  any admissible unvisited go to/open/examine over a searched place.
- **Kitchen fallback**: after burners, tables and counters once, broaden
  to unsearched cabinets, drawers, shelves, sinkbasins and carts.

## Strict search ledger (before every empty-handed search action)

1. If the required object is visible, take it now.
2. Otherwise choose an exact instance whose contents have NOT been
   observed in this object-search phase.
3. Reject any go to/examine/open for an exact instance already seen to
   lack the target -- even if it is likely, nearby, recently mentioned, or
   the destination type.
4. If all likely instances are rejected, broaden to any unvisited
   admissible place/class; never restart at instance 1 of a searched
   class.

The ledger survives inventory checks, appliance visits, placing the first
pick-two object, and putting down an inspected container; none of these
permits a rescan.

## Destination-as-source lockout

If the final receptacle type is also a plausible source, inspect each
visible instance at most once before pickup. After it lacks the target,
remember a usable destination instance and lock that exact instance out of
search until holding the ready object.

## Pick-two phase memory

After placing the first object, do not start a fresh room/class search.
Return directly to a remembered second source; otherwise continue from
the existing unsearched ledger, not from the beginning.

## Clean-task fallbacks

- **Kettle/teapot clean**: obvious countertops/islands, then stoveburners
  and sinkbasins once, then unsearched cabinets/shelves/carts/dining
  tables; remember one open cabinet as the destination, do not reuse
  searched cabinets as search targets.
- **Dishsponge clean**: sinkbasin and nearby countertops once, then
  unsearched cabinets/drawers/shelves/carts; remember the sink for cleaning
  and a shelf for delivery, do not return to them while empty-handed.
