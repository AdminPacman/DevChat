/**
 * graphLayering — Sugiyama phase 1, done so that CYCLES DO NOT COLLAPSE IT.
 *
 * THE BUG THIS EXISTS TO FIX (measured, 0018.05.26)
 * ------------------------------------------------
 * `WorkflowView`'s auto-layout ran Kahn's algorithm directly on the raw graph.
 * Kahn only ever dequeues a node once its indegree reaches zero — which for a
 * node inside a cycle never happens. Those nodes fell through to the
 * `if (!levelById.has(id)) levelById.set(id, 0)` fallback and were all assigned
 * **level 0**, i.e. the same column.
 *
 * Every crew workflow has a repair loop (Gate → Builder on NEEDS_WORK), so every
 * crew workflow is cyclic, so this fired constantly. Measured across
 * `yaml_instance/`:
 *
 *   data_visualization_enhanced_v3   13 nodes → tallest layer 13   (ALL of them)
 *   ChatDev_v1                       26 nodes → tallest layer 16
 *   kimi_next_phase                  14 nodes → tallest layer 12
 *   kimi_blocker_fix                  9 nodes → tallest layer  7
 *
 * A graph rendered as one tall column with every edge arcing back through it is
 * exactly what "everything was very compact" and "too many crossing wires"
 * describe. It was never a styling problem or a within-layer ordering problem —
 * crossings measured **zero** on these graphs, because with one real layer there
 * is no layer pair to cross between. It was the layering giving up.
 *
 * THE FIX
 * -------
 * Break cycles before layering. A depth-first search classifies each edge; an
 * edge pointing at a node currently on the DFS stack is a **back edge**. Remove
 * the back edges and what remains is a DAG that layers cleanly. The back edges
 * are not discarded — they are returned, so the canvas can draw them as what
 * they actually are: deliberate **return paths**, visually distinct from forward
 * flow rather than tangled in with it.
 *
 * This is the standard "feedback arc set by DFS" heuristic. It does not promise
 * the minimum feedback set (that problem is NP-hard) but it is linear, it is
 * deterministic, and it turns a collapsed column into a readable flow.
 *
 * DETERMINISM
 * -----------
 * Every iteration order here is fixed by the caller's node order (YAML
 * declaration order), never by Set/Map insertion accidents or hashing. The same
 * graph must lay out identically on every refresh — that is the whole point of
 * having fixed the layout-persistence bug in the first place.
 */

/**
 * Classify edges into forward (DAG) and backward (return paths) via DFS.
 *
 * @param {string[]} ids  node ids, in a stable order
 * @param {Map<string,Set<string>>} successors
 * @param {string[]} [roots]  preferred DFS start nodes (e.g. graph.start)
 * @returns {{acyclic: Map<string,Set<string>>, backEdges: Array<[string,string]>}}
 */
export function breakCycles(ids, successors, roots = []) {
  const WHITE = 0, GREY = 1, BLACK = 2
  const colour = new Map(ids.map(id => [id, WHITE]))
  const acyclic = new Map(ids.map(id => [id, new Set()]))
  const backEdges = []

  // Start from declared roots first, then indegree-0 nodes, then anything left.
  // This keeps the DFS tree — and therefore which edges get called "back" —
  // stable and sensible rather than dependent on iteration luck.
  const indegree = new Map(ids.map(id => [id, 0]))
  for (const from of ids) {
    for (const to of successors.get(from) || []) {
      if (indegree.has(to)) indegree.set(to, indegree.get(to) + 1)
    }
  }
  const startOrder = [
    ...roots.filter(id => colour.has(id)),
    ...ids.filter(id => indegree.get(id) === 0),
    ...ids,
  ]

  // Iterative DFS — these graphs are small, but a recursive version would blow
  // the stack on a pathological import and fail in a way nobody could read.
  for (const root of startOrder) {
    if (colour.get(root) !== WHITE) continue

    const stack = [{ id: root, iter: null }]
    colour.set(root, GREY)

    while (stack.length) {
      const frame = stack[stack.length - 1]
      if (frame.iter === null) {
        // Sort successors into the caller's stable id order.
        const order = new Map(ids.map((id, i) => [id, i]))
        frame.iter = Array.from(successors.get(frame.id) || [])
          .filter(t => colour.has(t))
          .sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
        frame.pos = 0
      }

      if (frame.pos >= frame.iter.length) {
        colour.set(frame.id, BLACK)
        stack.pop()
        continue
      }

      const target = frame.iter[frame.pos++]
      const state = colour.get(target)

      if (state === GREY) {
        // Points back at a node still open on the stack: this closes a cycle.
        backEdges.push([frame.id, target])
        continue
      }

      acyclic.get(frame.id).add(target)

      if (state === WHITE) {
        colour.set(target, GREY)
        stack.push({ id: target, iter: null })
      }
      // BLACK targets are cross/forward edges — safe to keep, no cycle.
    }
  }

  return { acyclic, backEdges }
}

/**
 * Assign every node a level using longest-path layering on the acyclic graph.
 *
 * Longest-path (rather than Kahn's "first time we reach it") is what makes an
 * edge always point strictly rightward: a node sits one level past its LATEST
 * predecessor, so no forward edge can ever run backwards or stay within a layer.
 *
 * @returns {{levelById: Map<string,number>, backEdges: Array<[string,string]>, collapsed: boolean}}
 */
export function assignLevels(ids, successors, roots = []) {
  if (!ids.length) return { levelById: new Map(), backEdges: [], collapsed: false }

  const { acyclic, backEdges } = breakCycles(ids, successors, roots)

  const indegree = new Map(ids.map(id => [id, 0]))
  for (const from of ids) {
    for (const to of acyclic.get(from) || []) indegree.set(to, indegree.get(to) + 1)
  }

  const levelById = new Map()
  const queue = ids.filter(id => indegree.get(id) === 0)
  queue.forEach(id => levelById.set(id, 0))

  let cursor = 0
  while (cursor < queue.length) {
    const id = queue[cursor++]
    const base = levelById.get(id) || 0
    for (const target of acyclic.get(id) || []) {
      levelById.set(target, Math.max(levelById.get(target) ?? 0, base + 1))
      indegree.set(target, indegree.get(target) - 1)
      if (indegree.get(target) === 0) queue.push(target)
    }
  }

  // With cycles removed this should be exhaustive. If anything is still
  // unlevelled the cycle-breaking missed a case — place it after its known
  // predecessors rather than dumping it at level 0, and report honestly.
  const stranded = ids.filter(id => !levelById.has(id))
  if (stranded.length) {
    console.warn(`[graphLayering] ${stranded.length} node(s) unlevelled after cycle-breaking:`, stranded)
    const predecessors = new Map(ids.map(id => [id, []]))
    for (const from of ids) {
      for (const to of successors.get(from) || []) predecessors.get(to)?.push(from)
    }
    for (const id of stranded) {
      const known = (predecessors.get(id) || [])
        .map(p => levelById.get(p))
        .filter(l => typeof l === 'number')
      levelById.set(id, known.length ? Math.max(...known) + 1 : 0)
    }
  }

  const levels = new Set(levelById.values())
  return {
    levelById,
    backEdges,
    // True when everything still landed in one column — the symptom this module
    // was written to eliminate. Surfaced so a regression is visible, not silent.
    collapsed: ids.length > 2 && levels.size <= 1,
  }
}
