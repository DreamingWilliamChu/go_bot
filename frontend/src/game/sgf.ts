// SGF import/export using @sabaki/sgf's parser/serializer.
// Export: root holds SZ/KM/RU/PL and the initial position as AB/AW; the main line holds real moves.
// Import: main line only. Setup properties after moves, or consecutive moves by the same colour,
// collapse the history so far into a new initial position (reported as a warning).

import { parse, stringify, type SgfNode } from '@sabaki/sgf'
import { SIZES, type BoardSize, sgfToXy, xyToSgf } from './coords'
import {
  type Color,
  type GameState,
  type Rules,
  type Setup,
  type Stone,
  collapseToSetup,
  fromSetup,
  nextPlayer,
  pass as passMove,
  play,
  validKomi,
} from './game'

const RULES_TO_SGF: Record<Rules, string> = {
  chinese: 'Chinese',
  japanese: 'Japanese',
  korean: 'Korean',
  aga: 'AGA',
  'new-zealand': 'NZ',
  'tromp-taylor': 'Tromp-Taylor',
  'stone-scoring': 'stone-scoring',
}

export function rulesFromSgf(ru: string | undefined): Rules | null {
  if (!ru) return null
  const r = ru.trim().toLowerCase().replace(/[\s_]+/g, '-')
  if (['chinese', 'cn', 'chinese-rules'].includes(r)) return 'chinese'
  if (['japanese', 'jp', 'japanese-rules'].includes(r)) return 'japanese'
  if (['korean', 'kr'].includes(r)) return 'korean'
  if (['aga', 'aga-rules'].includes(r)) return 'aga'
  if (['nz', 'new-zealand', 'newzealand'].includes(r)) return 'new-zealand'
  if (['tromp-taylor', 'tromptaylor', 'tt'].includes(r)) return 'tromp-taylor'
  if (['stone-scoring', 'stonescoring', 'ancient'].includes(r)) return 'stone-scoring'
  return null
}

export const PHOTO_NOTE =
  'Initial position reconstructed from a photo; earlier moves and ko history are unknown.'

let nextId = 0
function node(data: Record<string, string[]>, children: SgfNode[] = []): SgfNode {
  return { id: nextId++, data, parentId: null, children }
}

export function exportSgf(g: GameState, comment?: string): string {
  const n = g.setup.size
  const root: Record<string, string[]> = {
    GM: ['1'],
    FF: ['4'],
    CA: ['UTF-8'],
    AP: ['gobot:0.1'],
    SZ: [String(n)],
    KM: [String(g.setup.komi)],
    RU: [RULES_TO_SGF[g.setup.rules]],
    PL: [g.setup.initialPlayer],
  }
  const ab = g.setup.initialStones.filter((s) => s.color === 'B').map((s) => xyToSgf(s.x, s.y, n))
  const aw = g.setup.initialStones.filter((s) => s.color === 'W').map((s) => xyToSgf(s.x, s.y, n))
  if (ab.length) root.AB = ab
  if (aw.length) root.AW = aw
  const notes: string[] = []
  if (g.setup.source === 'photo' && !g.setup.comment?.includes(PHOTO_NOTE)) notes.push(PHOTO_NOTE)
  if (g.setup.comment) notes.push(g.setup.comment)
  if (comment) notes.push(comment)
  if (notes.length) root.C = [notes.join('\n')]
  const rootNode = node(root)
  let cur = rootNode
  for (const m of g.moves) {
    const v = m.x === null || m.y === null ? '' : xyToSgf(m.x, m.y, n)
    const child = node({ [m.color]: [v] })
    child.parentId = cur.id
    cur.children = [child]
    cur = child
  }
  return stringify([rootNode]) + '\n'
}

function expandPoints(values: string[], size: number): [number, number][] {
  const out: [number, number][] = []
  for (const v of values) {
    if (v.includes(':')) {
      const [a, b] = v.split(':')
      const p = sgfToXy(a, size)
      const q = sgfToXy(b, size)
      if (!p || !q) continue
      for (let x = Math.min(p[0], q[0]); x <= Math.max(p[0], q[0]); x++)
        for (let y = Math.min(p[1], q[1]); y <= Math.max(p[1], q[1]); y++) out.push([x, y])
    } else {
      const p = sgfToXy(v, size)
      if (p) out.push(p)
    }
  }
  return out
}

export interface ImportResult {
  game: GameState
  warnings: string[]
}

export function importSgf(text: string, defaults: { rules: Rules; komi: number }): ImportResult {
  let trees: SgfNode[]
  try {
    trees = parse(text)
  } catch (e) {
    throw new Error(`Could not read SGF: ${(e as Error).message}`)
  }
  if (!trees.length) throw new Error('The SGF file contains no game.')
  const root = trees[0]
  const warnings: string[] = []
  const d = root.data
  if (d.GM && d.GM[0] !== '1') throw new Error('This SGF file is not a Go game (GM is not 1).')
  const szRaw = (d.SZ?.[0] ?? '19').trim()
  if (szRaw.includes(':')) throw new Error('Rectangular boards are not supported.')
  const size = parseInt(szRaw, 10)
  if (!(SIZES as readonly number[]).includes(size))
    throw new Error(`Board size ${szRaw} is not supported (use 9, 13 or 19).`)
  let rules = rulesFromSgf(d.RU?.[0])
  if (!rules) {
    if (d.RU?.[0]) warnings.push(`Unknown rules "${d.RU[0]}"; using ${defaults.rules}.`)
    rules = defaults.rules
  }
  let komi = d.KM ? parseFloat(d.KM[0]) : defaults.komi
  if (!validKomi(komi)) {
    warnings.push(`Komi ${d.KM?.[0]} is not a multiple of 0.5; using ${defaults.komi}.`)
    komi = defaults.komi
  }
  const ha = d.HA ? parseInt(d.HA[0], 10) : 0

  let setup: Setup = {
    size: size as BoardSize,
    initialStones: [],
    initialPlayer: 'B',
    rules,
    komi,
    source: d.C?.[0]?.includes(PHOTO_NOTE) ? 'photo' : 'sgf',
    comment: d.C?.[0]?.replace(PHOTO_NOTE, '').trim() || undefined,
  }
  let game: GameState = fromSetup(setup)
  let plSeen: Color | null = null
  let moveNo = 0

  for (let cur: SgfNode | undefined = root; cur; cur = cur.children[0]) {
    const nd = cur.data
    const hasSetup = nd.AB || nd.AW || nd.AE
    if (hasSetup) {
      if (game.moves.length) {
        warnings.push(`Setup stones after move ${moveNo}: earlier moves were folded into the starting position.`)
        setup = collapseToSetup(game, 'sgf')
      } else {
        setup = game.setup
      }
      const map = new Map<string, Stone>(setup.initialStones.map((s) => [`${s.x},${s.y}`, s]))
      for (const [x, y] of expandPoints(nd.AE ?? [], size)) map.delete(`${x},${y}`)
      for (const [x, y] of expandPoints(nd.AB ?? [], size)) map.set(`${x},${y}`, { color: 'B', x, y })
      for (const [x, y] of expandPoints(nd.AW ?? [], size)) map.set(`${x},${y}`, { color: 'W', x, y })
      setup = { ...setup, initialStones: [...map.values()] }
      game = fromSetup(setup, game.revision + 1)
    }
    if (nd.PL && !game.moves.length) {
      const pl = nd.PL[0].toUpperCase()
      if (pl === 'B' || pl === 'W') {
        plSeen = pl
        game = { ...game, setup: { ...game.setup, initialPlayer: pl } }
      }
    }
    for (const color of ['B', 'W'] as Color[]) {
      if (!nd[color]) continue
      moveNo++
      if (!game.moves.length && !plSeen) {
        game = { ...game, setup: { ...game.setup, initialPlayer: color } }
      } else if (nextPlayer(game) !== color) {
        // e.g. handicap stones entered as consecutive black moves
        const s = collapseToSetup(game, 'sgf')
        game = fromSetup({ ...s, initialPlayer: color }, game.revision + 1)
        warnings.push(`Move ${moveNo} repeats ${color}; the position before it became the starting position.`)
      }
      const p = sgfToXy(nd[color][0] ?? '', size)
      if (p === null) {
        game = passMove(game)
      } else {
        const r = play(game, p[0], p[1])
        if (!r.ok) throw new Error(`Illegal move ${moveNo} (${color} ${xyToSgf(p[0], p[1], size)}): ${r.reason}`)
        game = r.game
      }
    }
  }
  if (!game.moves.length && !plSeen && ha >= 2 && game.setup.initialStones.length) {
    game = { ...game, setup: { ...game.setup, initialPlayer: 'W' } }
  }
  if (trees.length > 1) warnings.push('The file has several games; only the first was imported.')
  return { game, warnings }
}
