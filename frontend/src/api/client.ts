// REST + websocket client for the local gobot backend (same origin).

export interface PvStep {
  gtp: string
  x: number | null
  y: number | null
  pass: boolean
  color: 'B' | 'W'
  visits?: number
}

export interface Candidate {
  gtp: string
  x: number | null
  y: number | null
  pass: boolean
  order: number
  visits: number
  blackWinrate: number
  blackScoreLead: number
  prior: number
  lcb: number | null
  pv: PvStep[]
}

export interface AnalysisResult {
  type: 'partial' | 'final'
  requestId: string
  id: string
  isDuringSearch: boolean
  noResults: boolean
  perspective: 'black'
  currentPlayer?: 'B' | 'W'
  rootVisits?: number
  blackWinrate?: number
  blackScoreLead?: number
  scoreStdev?: number | null
  candidates: Candidate[]
  blackOwnership?: number[]
  elapsed: number
  cancelled: boolean
  timedOut: boolean
  engine?: { modelId: string | null; version: string | null; backend: string | null }
}

export interface EngineStatus {
  state: string
  modelId: string | null
  modelLabel: string | null
  modelFile: string | null
  modelInternalName: string | null
  modelDescription: string | null
  version: string | null
  backend: string | null
  backendKind: string | null
  error: string | null
  fallbackNote: string | null
  startupSeconds: number | null
}

export interface Preset {
  id: string
  label: string
  seconds: number
}

export interface StatusResponse {
  app: { version: string }
  engine: EngineStatus
  hardware: { summary: string; [k: string]: unknown }
  presets: Preset[]
  rules: string[]
  lan: boolean
  https: boolean
  tunnel: boolean
  recognition: RecognitionStatus | null
}

/** Who this browser is to the server (GET /api/auth/info, public). */
export interface AuthInfo {
  /** This browser must sign in (or pair) before it can use the app. */
  required: boolean
  signedIn: boolean
  user: string | null
  /** A browser on the Mac itself: always trusted. */
  mac: boolean
  passwordSet: boolean
  /** LAN pairing with the QR code is possible from here. */
  pairing: boolean
  /** Connected through the Cloudflare tunnel. */
  tunnel: boolean
}

export interface TunnelInfo {
  mode: 'quick' | 'named' | null
  state: 'off' | 'stopped' | 'starting' | 'running' | 'error'
  url: string | null
  error: string | null
}

export interface RecognitionStatus {
  detectors: string[]
  moku: { installed: boolean; file: string | null; error?: string | null }
}

export interface ModelInfo {
  id: string
  label: string
  role: string
  file: string
  architecture: string
  kata1Elo: number | null
  size: number
  license: string
  installed: boolean
  current: boolean
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg)
  }
  return (await r.json()) as T
}

export const api = {
  get: <T>(path: string) => fetch(path, { credentials: 'same-origin' }).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown) =>
    fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  upload: <T>(path: string, form: FormData) =>
    fetch(path, { method: 'POST', credentials: 'same-origin', body: form }).then((r) => handle<T>(r)),
}

export type SocketMessage =
  | AnalysisResult
  | { type: 'error'; requestId: string; kind: string; message: string }
  | { type: 'warning'; requestId: string; message: string }
  | { type: 'engine-starting'; requestId: string }
  | { type: 'pong' }

export type SocketState = 'connecting' | 'open' | 'closed' | 'unauthorized'

/** Websocket for analysis with automatic reconnect. */
export class AnalysisSocket {
  private ws: WebSocket | null = null
  private queue: string[] = []
  private retry = 0
  private stopped = false
  state: SocketState = 'connecting'

  constructor(
    private onMessage: (m: SocketMessage) => void,
    private onState: (s: SocketState) => void,
  ) {
    this.connect()
  }

  private url(): string {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${location.host}/api/analyze`
  }

  private setState(s: SocketState) {
    this.state = s
    this.onState(s)
  }

  private connect() {
    if (this.stopped) return
    this.setState('connecting')
    const ws = new WebSocket(this.url())
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.setState('open')
      for (const m of this.queue.splice(0)) ws.send(m)
    }
    ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data as string) as SocketMessage)
      } catch {
        /* ignore malformed */
      }
    }
    ws.onclose = (ev) => {
      this.ws = null
      if (ev.code === 4401) {
        this.setState('unauthorized')
        return
      }
      this.setState('closed')
      const delay = Math.min(10000, 500 * 2 ** this.retry++)
      setTimeout(() => this.connect(), delay)
    }
  }

  reconnect() {
    this.stopped = false
    if (!this.ws) this.connect()
  }

  send(obj: unknown) {
    const s = JSON.stringify(obj)
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(s)
    else this.queue.push(s)
  }

  analyze(requestId: string, position: unknown, params: unknown) {
    // Drop any queued analyze requests: only the newest position matters.
    this.queue = this.queue.filter((q) => !q.includes('"type":"analyze"'))
    this.send({ type: 'analyze', requestId, position, params })
  }

  cancel(requestId: string) {
    this.send({ type: 'cancel', requestId })
  }

  close() {
    this.stopped = true
    this.ws?.close()
  }
}
