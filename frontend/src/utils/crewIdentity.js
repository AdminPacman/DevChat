/**
 * crewIdentity — who is who on the canvas, decided once and never by chance.
 *
 * THE PROBLEM THIS REPLACES
 * ------------------------
 * `spriteFetcher` handed out a random 1-of-12 sprite per node_id from an
 * in-memory Map, and `colorUtils` picked a palette by hashing the type string.
 * Two consequences, both wrong:
 *
 *   1. The map lives in memory, so **every refresh reshuffled the whole crew.**
 *      Kimi did not look like Kimi twice in a row.
 *   2. Neither was type-aware, so a human gate and an agent drew from the same
 *      pool. Pac's own approval node could come back as any of twelve strangers.
 *
 * You cannot learn a graph whose faces move. Identity here is resolved from what
 * a node *is* — its type, and which fleet binary it dispatches to — so it is
 * stable across refreshes, across workflows, and across machines.
 *
 * THE VISUAL LANGUAGE TEACHES THE LAW
 * -----------------------------------
 * SCRIPTS MEASURE, AGENTS JUDGE. So:
 *
 *   - Agents get **faces**, because they exercise judgement and you need to know
 *     whose judgement you are reading.
 *   - Machinery (literal, python, loop counters) is deliberately **faceless and
 *     flat** — no sprite, no gradient. That absence is the signal, not an
 *     oversight. A node with no face made no judgement.
 *
 * BRANDING — inherited, not invented
 * ----------------------------------
 * Per brand-kit-doctrine the kit provides the machinery and each community
 * supplies its own voice. This console is crew infrastructure, so its voice is
 * **Pac's Arcade**, never onecocreation's. The palette below is lifted straight
 * from the arcade's own mark, TRICOLOR PAC
 * (`~/dev/pacsarcade/brand-assets/tricolor-pac/RECIPE-tricolor-pac.md`), whose
 * three locked layers are warm amber / red core / cool teal-green.
 *
 * The mapping is the nice part: **the tricolor's three layers become the three
 * fleet agents, and PAC is the core.**
 *
 *   warm amber  #F9CF02  → Kimi    the guest builder
 *   red core    #FF3D34  → PAC     the Admiral, the centre of the mark
 *   cool teal   #1C8A63  → pi      the local hand
 *   vivid cyan  #1EC8E6  → Claude  the gate/judge (vivid-set cool layer)
 *
 * Note on gold: on Love's site gold means money and may not be borrowed to mean
 * "important". That is a onecocreation law and does not travel — here amber is a
 * layer of the arcade's own mark, carrying no money semantics at all.
 */

/** Fleet tool name → crew key. These are the dispatchers in
 *  `functions/function_calling/`; a node that calls one IS that crew member. */
const FLEET_TOOL_TO_CREW = {
  kimi_run: 'kimi',
  claude_run: 'claude',
  pi_run: 'pi',
}

/** Node types that are machinery — they measure, they do not judge. */
const FACELESS_TYPES = new Set([
  'literal',
  'python',
  'loop_counter',
  'counter',
  'start-node',
  // A passthrough forwards a message unchanged. It exercises no judgement, so
  // by the same law it gets no face — and it stops consuming a sprite that a
  // real agent needs.
  'passthrough',
  'branch',
  'switch',
  'merge',
  'aggregator',
])

/**
 * The registry. `sprite` is a character index into /sprites/{n}-{stance}-{frame}.png,
 * chosen by eye against the tricolor palette so the face and the colour agree —
 * a warm-dressed sprite for the amber layer, a blue-haired one for the cool layer.
 *
 * To re-cast a crew member, change one `sprite` number here. Nothing else knows.
 */
export const CREW = {
  kimi: {
    key: 'kimi',
    label: 'Kimi',
    role: 'the guest builder',
    sprite: 7, // blonde, amber apron — the warm layer
    accent: '#F9CF02',
    gradient: 'linear-gradient(135deg, #fde68a, #f9cf02, #e0a800)',
    shadow: 'rgba(249, 207, 2, 0.45)',
    faceless: false,
  },
  claude: {
    key: 'claude',
    label: 'Claude',
    role: 'the gate — judges, does not build',
    sprite: 5, // blue hair — the cool layer
    accent: '#1EC8E6',
    gradient: 'linear-gradient(135deg, #a9ecfa, #1ec8e6, #0e93ad)',
    shadow: 'rgba(30, 200, 230, 0.45)',
    faceless: false,
  },
  pi: {
    key: 'pi',
    label: 'pi',
    role: 'the local hand',
    sprite: 1, // green hair — the teal-green layer
    accent: '#1C8A63',
    gradient: 'linear-gradient(135deg, #9fe3c6, #1c8a63, #106345)',
    shadow: 'rgba(28, 138, 99, 0.45)',
    faceless: false,
  },
  pac: {
    key: 'pac',
    label: 'PAC',
    role: 'the Admiral — the gate that is a person',
    // Not a stock sprite. Pac's node wears the arcade's own mark.
    avatarOverride: '/brand/tricolor-pac.png',
    sprite: null,
    accent: '#FF3D34',
    gradient: 'linear-gradient(135deg, #ffb3ae, #ff3d34, #c41f18)',
    shadow: 'rgba(255, 61, 52, 0.55)',
    faceless: false,
    loud: true, // the loudest thing on the canvas when it is waiting on you
  },
  agent: {
    key: 'agent',
    label: 'Agent',
    role: 'an LLM node with no fleet binary behind it',
    sprite: null, // resolved per-node, deterministically, from the id
    accent: '#7E8FD6', // the tricolor composite body — periwinkle
    gradient: 'linear-gradient(135deg, #c3ccf0, #7e8fd6, #5566ab)',
    shadow: 'rgba(126, 143, 214, 0.4)',
    faceless: false,
  },
  machine: {
    key: 'machine',
    label: 'Machinery',
    role: 'measures — deliberately faceless',
    sprite: null,
    accent: '#7A8290',
    gradient: 'linear-gradient(135deg, #8b93a1, #7a8290)', // flat-ish on purpose
    shadow: 'rgba(122, 130, 144, 0.3)',
    faceless: true,
  },
}

/** Sprites an unnamed agent may be cast from — everything not already spoken
 *  for by a named crew member, so a generic agent never impersonates Kimi. */
const GENERIC_SPRITE_POOL = [2, 3, 4, 6, 8, 9, 10, 11, 12]

/**
 * Stable string hash (FNV-1a). Deterministic across refreshes, sessions and
 * machines — which is the entire point. Never Math.random().
 */
function stableHash(str) {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0)
}

/**
 * Pull every fleet tool name declared on a node.
 * Shape (from the YAML, handed to the frontend verbatim as `node.data`):
 *   config.tooling[].config.tools[].name
 */
function fleetToolsOf(node) {
  const tooling = node?.config?.tooling
  if (!Array.isArray(tooling)) return []
  const names = []
  for (const block of tooling) {
    const tools = block?.config?.tools
    if (!Array.isArray(tools)) continue
    for (const tool of tools) {
      const name = typeof tool === 'string' ? tool : tool?.name
      if (name) names.push(name)
    }
  }
  return names
}

/**
 * Resolve a node to its crew identity. Order matters: a human node is PAC even
 * if it somehow also declares a tool, because the person outranks the machinery.
 *
 * @param {Object} node - the raw YAML node (what LaunchView puts in `data`)
 * @returns {Object} an entry from CREW, never null
 */
export function resolveIdentity(node) {
  if (!node) return CREW.machine

  const type = node.type || node.data?.type || 'unknown'
  const source = node.config ? node : (node.data || node)

  // 1. A person is a person.
  if (type === 'human') return CREW.pac

  // 2. A node that dispatches to a fleet binary IS that crew member.
  for (const toolName of fleetToolsOf(source)) {
    const crewKey = FLEET_TOOL_TO_CREW[toolName]
    if (crewKey) return CREW[crewKey]
  }

  // 3. Machinery measures. No face.
  if (FACELESS_TYPES.has(type)) return CREW.machine

  // 4. Anything else that thinks is a generic agent.
  return CREW.agent
}

/**
 * The sprite character index for a node — deterministic, and stable forever.
 * Named crew keep their cast face; generic agents are drawn from the leftover
 * pool by a hash of their id, so the same node is the same person every time.
 *
 * @returns {number|null} character index, or null when the node is faceless
 */
export function spriteCharacterFor(node, identity = null) {
  const id = identity || resolveIdentity(node)
  if (id.faceless) return null
  // A crew member with its own artwork has no sprite index at all — asking for
  // one must NOT fall through to the generic pool, or PAC gets cast as a stranger.
  if (id.avatarOverride) return null
  if (id.sprite) return id.sprite

  const nodeId = String(node?.id || node?.data?.id || '')
  if (!nodeId) return GENERIC_SPRITE_POOL[0]
  // Prefer the graph-wide binding (collision-free); fall back to a bare hash so a
  // node rendered before bindGraph() still gets a stable face rather than none.
  if (boundSprites.has(nodeId)) return boundSprites.get(nodeId)
  return GENERIC_SPRITE_POOL[stableHash(nodeId) % GENERIC_SPRITE_POOL.length]
}

/**
 * Generic-agent casting, resolved across the WHOLE graph rather than per node.
 *
 * Hashing each id independently into a 9-sprite pool collides constantly — with
 * nine slots, two agents in a graph of six share a face about half the time, and
 * two identical faces is the very bug this module exists to remove. So we bind
 * once per graph: walk the generic agents in a deterministic order and give each
 * its hash-preferred sprite, probing forward when that slot is taken.
 *
 * Deterministic inputs only (sorted ids, a fixed hash), so the same graph casts
 * the same way on every refresh and on every machine. Call it whenever the node
 * list changes; it is cheap and idempotent.
 */
const boundSprites = new Map()

export function bindGraph(nodes) {
  boundSprites.clear()
  if (!Array.isArray(nodes)) return

  const generics = nodes
    .map(n => (n?.data && n.data.type) ? n.data : n)
    .filter(n => n && resolveIdentity(n) === CREW.agent)
    .map(n => String(n.id || ''))
    .filter(Boolean)
    .sort() // deterministic order, independent of YAML/array ordering

  const taken = new Set()
  for (const id of generics) {
    const pool = GENERIC_SPRITE_POOL
    const preferred = stableHash(id) % pool.length
    let chosen = pool[preferred]
    for (let i = 0; i < pool.length; i++) {
      const candidate = pool[(preferred + i) % pool.length]
      if (!taken.has(candidate)) { chosen = candidate; break }
    }
    // More generic agents than sprites: the pool wraps and faces repeat. That is
    // honest — better a repeat than a silent blank — but say so out loud once.
    if (taken.has(chosen)) {
      console.warn(`[crewIdentity] sprite pool exhausted (${pool.length}); "${id}" reuses a face.`)
    }
    taken.add(chosen)
    boundSprites.set(id, chosen)
  }
}

/**
 * The final image path for a node's face — the one call site everything else
 * should use. Returns '' for machinery, which renders no avatar at all.
 *
 * @param {Object} node   the raw YAML node
 * @param {string} stance 'D' | 'L' | 'R' | 'U'
 * @param {number} frame  1 | 2 | 3
 */
export function avatarFor(node, stance = 'D', frame = 1) {
  const id = resolveIdentity(node)
  if (id.faceless) return ''
  if (id.avatarOverride) return id.avatarOverride // single-frame artwork, no walk cycle
  const character = spriteCharacterFor(node, id)
  if (!character) return ''
  return `/sprites/${character}-${stance}-${frame}.png`
}

/** CSS custom properties for a node, driven by identity rather than a type hash. */
export function identityStyles(node) {
  const id = resolveIdentity(node)
  return {
    '--node-gradient': id.gradient,
    '--node-shadow-color': id.shadow,
    '--crew-accent': id.accent,
  }
}

export { stableHash }
