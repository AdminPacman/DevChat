/**
 * layerOrdering — Sugiyama phase 2, the step the canvas was missing.
 *
 * THE PROBLEM
 * -----------
 * The auto-layout runs Kahn's algorithm to assign every node a level (phase 1:
 * layering) and then places the nodes of each level top-to-bottom in whatever
 * order they happened to land in the bucket — which is YAML declaration order,
 * i.e. arbitrary. Arbitrary within-layer order is *precisely* what produces
 * crossing wires. No amount of edge styling fixes it, because crossings are a
 * layout problem, not a rendering problem.
 *
 * THE FIX
 * -------
 * Phase 2 of Sugiyama: repeatedly reorder each layer by the **median position of
 * each node's neighbours in the adjacent layer**, sweeping forward and backward.
 * Two to four sweeps removes most crossings. This is textbook and it is small.
 *
 * Median rather than mean (barycentre): the median is markedly more robust when
 * one node has a single far-flung neighbour, which is common here — a repair
 * loop counter wired back to a builder six layers up would drag a mean ordering
 * badly out of shape.
 *
 * HONEST MEASUREMENT
 * ------------------
 * `countCrossings` exists so the improvement is measured, not asserted. Scripts
 * measure; that applies to our own tooling too. If a sweep makes things worse on
 * some graph, the counter will say so rather than let us claim a win.
 */

/**
 * Count edge crossings between adjacent layers for a given ordering.
 *
 * Two edges (u1→v1) and (u2→v2) between the same layer pair cross exactly when
 * their endpoints are in opposite relative order. This is the standard O(k²)
 * pair count — fine for graphs of the size a human reads.
 *
 * @param {Map<number,string[]>} order      level -> ordered node ids
 * @param {number[]}             levelKeys  ascending level numbers
 * @param {Map<string,Set<string>>} successors  id -> set of downstream ids
 * @returns {number} total crossings across all adjacent layer pairs
 */
export function countCrossings(order, levelKeys, successors) {
  let total = 0

  for (let k = 0; k < levelKeys.length - 1; k++) {
    const upper = order.get(levelKeys[k]) || []
    const lower = order.get(levelKeys[k + 1]) || []

    const lowerIndex = new Map()
    lower.forEach((id, i) => lowerIndex.set(id, i))

    // Collect edges from this layer to the next, as (upperPos, lowerPos) pairs.
    const pairs = []
    upper.forEach((id, uPos) => {
      for (const target of successors.get(id) || []) {
        const lPos = lowerIndex.get(target)
        if (typeof lPos === 'number') pairs.push([uPos, lPos])
      }
    })

    for (let i = 0; i < pairs.length; i++) {
      for (let j = i + 1; j < pairs.length; j++) {
        const [u1, v1] = pairs[i]
        const [u2, v2] = pairs[j]
        if ((u1 - u2) * (v1 - v2) < 0) total++
      }
    }
  }

  return total
}

/** Median of a numeric array. Returns null for an empty array. */
function median(values) {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * Reorder each layer to minimise crossings.
 *
 * @param {Object}  opts
 * @param {Map<number,string[]>}    opts.buckets      level -> ids (any order)
 * @param {number[]}                opts.levelKeys    ascending level numbers
 * @param {Map<string,Set<string>>} opts.predecessors id -> upstream ids
 * @param {Map<string,Set<string>>} opts.successors   id -> downstream ids
 * @param {number}  [opts.sweeps=4]  forward/backward passes
 * @returns {{order: Map<number,string[]>, before: number, after: number}}
 */
export function orderLayers({ buckets, levelKeys, predecessors, successors, sweeps = 4 }) {
  const order = new Map(levelKeys.map(lvl => [lvl, [...(buckets.get(lvl) || [])]]))

  const before = countCrossings(order, levelKeys, successors)
  let best = new Map(Array.from(order, ([k, v]) => [k, [...v]]))
  let bestScore = before

  for (let sweep = 0; sweep < sweeps; sweep++) {
    // Alternate direction: forward sweeps order a layer by its predecessors,
    // backward sweeps by its successors. Alternating is what lets the ordering
    // settle instead of oscillating around one side of the graph.
    const forward = sweep % 2 === 0
    const targets = forward
      ? levelKeys.slice(1)
      : levelKeys.slice(0, -1).reverse()

    for (const lvl of targets) {
      const adjacentLevel = forward ? lvl - 1 : lvl + 1
      const adjacent = order.get(adjacentLevel) || []
      const adjacentIndex = new Map()
      adjacent.forEach((id, i) => adjacentIndex.set(id, i))

      const current = order.get(lvl) || []
      const scored = current.map((id, position) => {
        const neighbours = Array.from((forward ? predecessors.get(id) : successors.get(id)) || [])
        const positions = neighbours
          .map(n => adjacentIndex.get(n))
          .filter(v => typeof v === 'number')
        const m = median(positions)
        // A node with no neighbours in the adjacent layer has no opinion —
        // keep it where it is rather than slamming it to the top.
        return { id, key: m === null ? position : m, position }
      })

      // Tie-break on existing position, so the sort is stable and the result is
      // deterministic — the same graph must lay out identically every time.
      scored.sort((a, b) => (a.key - b.key) || (a.position - b.position))
      order.set(lvl, scored.map(s => s.id))
    }

    // Keep the best ordering seen, not merely the last one. Sweeps are not
    // monotonic — a later sweep can be worse, and shipping it would be a
    // regression we told ourselves was an improvement.
    const score = countCrossings(order, levelKeys, successors)
    if (score < bestScore) {
      bestScore = score
      best = new Map(Array.from(order, ([k, v]) => [k, [...v]]))
    }
  }

  return { order: best, before, after: bestScore }
}
