// Immutable game state. The position from a photo (or manual setup) is the *initial* position:
// we never invent the moves that led to it. Moves played afterwards are real history and are sent
// to KataGo as `moves`, so captures/ko/superko apply from that point on.
//
// Rules for move entry come from @sabaki/go-board (captures, suicide, simple ko). KataGo applies
// the full ruleset (including superko) when analysing and reports illegal moves as errors.

import GoBoard from '@sabaki/go-board'
import type { Sign } from '@sabaki/go-board'
import type { BoardSize } from './coords'

export type Color = 'B' | 'W'
export type Rules =
  | 'chinese'
  | 'japanese'
  | 'korean'
  | 'aga'
  | 'tromp-taylor'
  | 'new-zealand'
  | 'stone-scoring'

export const RULES: { id: Rules; label: string }[] = [
  { id: 'chinese', label: 'Chinese (area)' },
  { id: 'japanese', label: 'Japanese (territory)' },
  { id: 'korean', label: 'Korean (territory)' },
  { id: 'aga', label: 'AGA' },
  { id: 'new-zealand', label: 'New Zealand' },
  { id: 'tromp-taylor', label: 'Tromp-Taylor' },
  { id: 'stone-scoring', label: 'Stone scoring' },
]

export interface Stone {
  color: Color
  x: number
  y: number
}

export interface MoveRec {
  color: Color // for an erase: the colour of the removed stone
  x: number | null // null = pass
  y: number | null
  erase?: true // a stone taken off the board (not a move; the turn does not change)
}

export interface Setup {
  size: BoardSize
  initialStones: Stone[]
  initialPlayer: Color
  rules: Rules
  komi: number
  source: 'empty' | 'manual' | 'photo' | 'sgf'
  comment?: string
}

export interface GameState {
  setup: Setup
  moves: MoveRec[]
  redo: MoveRec[]
  revision: number
}

export const DEFAULT_KOMI: Record<Rules, number> = {
  chinese: 7.5,
  japanese: 6.5,
  korean: 6.5,
  aga: 7.5,
  'new-zealand': 7,
  'tromp-taylor': 7.5,
  'stone-scoring': 7.5,
}

export function other(c: Color): Color {
  return c === 'B' ? 'W' : 'B'
}

export function sign(c: Color): Sign {
  return c === 'B' ? 1 : -1
}

export function newGame(size: BoardSize = 19, rules: Rules = 'chinese', komi = 7.5): GameState {
  return {
    setup: { size, initialStones: [], initialPlayer: 'B', rules, komi, source: 'empty' },
    moves: [],
    redo: [],
    revision: 0,
  }
}

export function fromSetup(setup: Setup, revision = 0): GameState {
  return { setup: { ...setup, initialStones: dedupe(setup.initialStones) }, moves: [], redo: [], revision }
}

function dedupe(stones: Stone[]): Stone[] {
  const m = new Map<string, Stone>()
  for (const s of stones) m.set(`${s.x},${s.y}`, s)
  return [...m.values()]
}

export function initialBoard(setup: Setup): GoBoard {
  let b = GoBoard.fromDimensions(setup.size)
  for (const s of setup.initialStones) b = b.set([s.x, s.y], sign(s.color))
  return b
}

function clearKo(b: GoBoard): GoBoard {
  const c = b.clone()
  ;(c as unknown as { _koInfo: unknown })._koInfo = { sign: 0, vertex: [-1, -1] }
  return c
}

export function applyMove(b: GoBoard, m: MoveRec): GoBoard {
  if (m.x === null || m.y === null) return clearKo(b)
  if (m.erase) return clearKo(b.set([m.x, m.y], 0))
  return b.makeMove(sign(m.color), [m.x, m.y], {
    preventOverwrite: true,
    preventSuicide: true,
    preventKo: true,
  })
}

/** Board after all moves (throws only if the stored history is inconsistent). */
export function currentBoard(g: GameState): GoBoard {
  let b = initialBoard(g.setup)
  for (const m of g.moves) b = applyMove(b, m)
  return b
}

export function boardAt(g: GameState, moveCount: number): GoBoard {
  let b = initialBoard(g.setup)
  for (const m of g.moves.slice(0, moveCount)) b = applyMove(b, m)
  return b
}

export function nextPlayer(g: GameState): Color {
  for (let i = g.moves.length - 1; i >= 0; i--) if (!g.moves[i].erase) return other(g.moves[i].color)
  return g.setup.initialPlayer
}

/** Moves played (passes count, erases do not). */
export function moveCount(g: GameState): number {
  return g.moves.filter((m) => !m.erase).length
}

export type PlayResult = { ok: true; game: GameState } | { ok: false; reason: string }

/** `color` overrides whose turn it is, e.g. to place several stones of one colour in a row. */
export function play(g: GameState, x: number, y: number, color: Color = nextPlayer(g)): PlayResult {
  const before = currentBoard(g)
  try {
    applyMove(before, { color, x, y })
  } catch (e) {
    const msg = String((e as Error).message || e)
    if (msg.includes('Overwrite')) return { ok: false, reason: 'That point is occupied.' }
    if (msg.includes('Suicide')) return { ok: false, reason: 'Suicide is not allowed.' }
    if (msg.includes('Ko')) return { ok: false, reason: 'Ko: you must play elsewhere first.' }
    return { ok: false, reason: msg }
  }
  return {
    ok: true,
    game: { ...g, moves: [...g.moves, { color, x, y }], redo: [], revision: g.revision + 1 },
  }
}

export function pass(g: GameState, color: Color = nextPlayer(g)): GameState {
  return { ...g, moves: [...g.moves, { color, x: null, y: null }], redo: [], revision: g.revision + 1 }
}

/** Take one stone off the board, kept in the history so Undo puts it back. Null on an empty point. */
export function eraseStone(g: GameState, x: number, y: number): GameState | null {
  const s = currentBoard(g).get([x, y])
  if (!s) return null
  const rec: MoveRec = { color: s > 0 ? 'B' : 'W', x, y, erase: true }
  return { ...g, moves: [...g.moves, rec], redo: [], revision: g.revision + 1 }
}

export function undo(g: GameState): GameState {
  if (g.moves.length === 0) return g
  const last = g.moves[g.moves.length - 1]
  return { ...g, moves: g.moves.slice(0, -1), redo: [last, ...g.redo], revision: g.revision + 1 }
}

export function redo(g: GameState): GameState {
  if (g.redo.length === 0) return g
  const [first, ...rest] = g.redo
  return { ...g, moves: [...g.moves, first], redo: rest, revision: g.revision + 1 }
}

export function captures(g: GameState): { B: number; W: number } {
  const b = currentBoard(g)
  return { B: b.getCaptures(1), W: b.getCaptures(-1) }
}

/** Collapse the current position (after all moves) into a new initial position. */
export function collapseToSetup(g: GameState, source: Setup['source'] = 'manual'): Setup {
  const b = currentBoard(g)
  const stones: Stone[] = []
  for (let y = 0; y < g.setup.size; y++)
    for (let x = 0; x < g.setup.size; x++) {
      const s = b.get([x, y])
      if (s === 1) stones.push({ color: 'B', x, y })
      else if (s === -1) stones.push({ color: 'W', x, y })
    }
  return { ...g.setup, initialStones: stones, initialPlayer: nextPlayer(g), source }
}

/** Setup-mode edit: set a point to a colour or empty (only allowed before any move). */
export function setPoint(setup: Setup, x: number, y: number, value: Color | null): Setup {
  const rest = setup.initialStones.filter((s) => !(s.x === x && s.y === y))
  return { ...setup, initialStones: value ? [...rest, { color: value, x, y }] : rest, source: 'manual' }
}

/** Groups without liberties in a setup position (would be illegal / already captured). */
export function deadGroupsInSetup(setup: Setup): Stone[] {
  const b = initialBoard(setup)
  const out: Stone[] = []
  for (const s of setup.initialStones) {
    if (!b.hasLiberties([s.x, s.y])) out.push(s)
  }
  return out
}

export function validKomi(k: number): boolean {
  return Number.isFinite(k) && Math.abs(k) <= 150 && Math.abs(k * 2 - Math.round(k * 2)) < 1e-9
}

/** Payload for the backend / KataGo. `toPlay` overrides alternation (Keep color).
 *  KataGo's move list has no erases, so the history up to the last erase becomes the initial position. */
export function toApiPosition(g: GameState, toPlay?: Color | null) {
  let k = g.moves.length - 1
  while (k >= 0 && !g.moves[k].erase) k--
  if (k >= 0) g = { ...g, setup: collapseToSetup({ ...g, moves: g.moves.slice(0, k + 1) }), moves: g.moves.slice(k + 1) }
  return {
    size: g.setup.size,
    initial_stones: g.setup.initialStones.map((s) => ({ color: s.color, x: s.x, y: s.y })),
    initial_player: g.setup.initialPlayer,
    moves: g.moves.map((m) => ({ color: m.color, x: m.x, y: m.y })),
    rules: g.setup.rules,
    komi: g.setup.komi,
    ...(toPlay ? { to_play: toPlay } : {}),
  }
}
