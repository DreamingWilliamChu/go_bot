import { useEffect, useState } from 'preact/hooks'
import { api, type AuthInfo, type ModelInfo, type StatusResponse, type TunnelInfo } from '../api/client'

interface Props {
  status: StatusResponse | null
  auth: AuthInfo | null
  refreshStatus: () => void
  toast: (m: string) => void
}

export function SettingsScreen({ status, auth, refreshStatus, toast }: Props) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [switching, setSwitching] = useState<string | null>(null)
  const [pairInfo, setPairInfo] = useState<{ url: string | null; lan: boolean; https: boolean } | null>(null)

  useEffect(() => {
    api
      .get<{ models: ModelInfo[] }>('/api/models')
      .then((r) => setModels(r.models))
      .catch(() => setModels([]))
    if (auth?.mac)
      api
        .get<{ url: string | null; lan: boolean; https: boolean }>('/api/pair/info')
        .then(setPairInfo)
        .catch(() => setPairInfo(null))
  }, [status?.engine.modelId, auth?.mac])

  async function switchModel(id: string) {
    setSwitching(id)
    try {
      await api.post('/api/engine/model', { modelId: id })
      toast('Model loaded')
    } catch (e) {
      toast((e as Error).message)
    } finally {
      setSwitching(null)
      refreshStatus()
    }
  }

  const eng = status?.engine
  return (
    <div class="screen settings-screen">
      <section class="panel">
        <h2>Engine</h2>
        <dl class="kv small">
          <dt>State</dt>
          <dd data-testid="engine-state">{eng?.state ?? '–'}</dd>
          <dt>KataGo</dt>
          <dd>{eng?.version ? `v${eng.version}` : '–'}</dd>
          <dt>Backend</dt>
          <dd>{eng?.backend ?? '–'}</dd>
          <dt>Network</dt>
          <dd>{eng?.modelInternalName ?? eng?.modelFile ?? '–'}</dd>
          <dt>Startup</dt>
          <dd>{eng?.startupSeconds != null ? `${eng.startupSeconds} s` : '–'}</dd>
          <dt>Computer</dt>
          <dd>{status?.hardware.summary ?? '–'}</dd>
        </dl>
        {eng?.error ? <p class="note error small">{eng.error}</p> : null}
        {eng?.fallbackNote ? <p class="note warn small">{eng.fallbackNote}</p> : null}
        <h3>Neural network</h3>
        <ul class="models">
          {models.map((m) => (
            <li key={m.id} class={m.current ? 'current' : ''}>
              <div>
                <b>{m.label}</b>
                <div class="muted tiny">
                  {m.architecture} · {(m.size / 1e6).toFixed(0)} MB · kata1 Elo {m.kata1Elo ?? '–'}
                </div>
              </div>
              {m.current ? (
                <span class="pill">in use</span>
              ) : m.installed ? (
                <button class="btn small" disabled={!!switching} onClick={() => switchModel(m.id)}>
                  {switching === m.id ? 'Loading…' : 'Use'}
                </button>
              ) : (
                <span class="muted tiny">not downloaded</span>
              )}
            </li>
          ))}
        </ul>
        <p class="muted tiny">
          Elo values are from KataGo's training-run rating games, not a measurement on this computer. Run{' '}
          <code>./scripts/benchmark.sh</code> on the Mac to measure speed and pick the default.
        </p>
      </section>

      <section class="panel">
        <h2>Connect your phone</h2>
        {auth?.mac ? (
          pairInfo?.url ? (
            <div class="pair">
              <img src="/api/pair/qr.svg" alt="Pairing QR code" width={220} height={220} />
              <p class="small">
                Scan with the iPhone camera (same Wi-Fi), or open:
                <br />
                <code class="wrap">{pairInfo.url}</code>
              </p>
            </div>
          ) : (
            <p class="small">
              Phone access is off. Quit the server and start it with <code>./scripts/start.sh --lan</code> (add{' '}
              <code>--https</code> for the live camera view). A QR code will appear here and in the terminal.
            </p>
          )
        ) : (
          <p class="small">You are connected from another device{status?.https ? ' over HTTPS' : ''}.</p>
        )}
        <p class="muted tiny">
          "Take photo" and "Choose photo" work over plain HTTP. Only the optional live camera preview needs HTTPS and a
          one-time certificate install on the phone (see docs/PHONE_SETUP.md).
        </p>
      </section>

      <RemoteAccess enabled={!!status?.tunnel} />
      {auth ? <AccountSection auth={auth} toast={toast} /> : null}
    </div>
  )
}

function RemoteAccess({ enabled }: { enabled: boolean }) {
  const [tunnel, setTunnel] = useState<TunnelInfo | null>(null)

  useEffect(() => {
    if (!enabled) return
    let timer: number | undefined
    const poll = () =>
      api
        .get<TunnelInfo>('/api/tunnel')
        .then((t) => {
          setTunnel(t)
          if (t.state === 'starting') timer = window.setTimeout(poll, 2000)
        })
        .catch(() => setTunnel(null))
    poll()
    return () => window.clearTimeout(timer)
  }, [enabled])

  return (
    <section class="panel" data-testid="remote-access">
      <h2>Remote access</h2>
      {!enabled ? (
        <p class="small">
          Off. To use the app from anywhere (mobile data, another Wi-Fi), set a sign-in password below, then quit the
          server and start it with <code>./scripts/start.sh --tunnel</code>. It connects through a Cloudflare tunnel;
          see docs/REMOTE_ACCESS.md.
        </p>
      ) : tunnel?.state === 'running' && tunnel.url ? (
        <div class="pair">
          <img src="/api/tunnel/qr.svg" alt="QR code for the remote address" width={220} height={220} />
          <p class="small">
            Open from anywhere:
            <br />
            <a href={tunnel.url} target="_blank" rel="noreferrer">
              <code class="wrap">{tunnel.url}</code>
            </a>
          </p>
        </div>
      ) : tunnel?.state === 'error' ? (
        <p class="note error small">{tunnel.error}</p>
      ) : (
        <p class="small">Starting the Cloudflare tunnel…</p>
      )}
      {enabled ? (
        <p class="muted tiny">
          Anyone can open this address, but only your username and password let them in.
          {tunnel?.mode === 'quick' ? ' A quick tunnel gets a new address every time the server starts.' : ''}
        </p>
      ) : null}
    </section>
  )
}

function AccountSection({ auth, toast }: { auth: AuthInfo; toast: (m: string) => void }) {
  const [account, setAccount] = useState<{ username: string; passwordSet: boolean } | null>(null)
  const [username, setUsername] = useState('')
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<{ username: string; passwordSet: boolean }>('/api/account')
      .then((a) => {
        setAccount(a)
        setUsername(a.username)
      })
      .catch(() => setAccount(null))
  }, [])

  if (!account) return null
  const needCurrent = !auth.mac
  const mismatch = repeat.length > 0 && next !== repeat

  async function save(e: Event) {
    e.preventDefault()
    if (next !== repeat) return setError('The new passwords do not match.')
    setBusy(true)
    setError(null)
    try {
      const a = await api.post<{ username: string; passwordSet: boolean }>('/api/account', {
        currentPassword: needCurrent ? current : undefined,
        username: username.trim(),
        newPassword: next || undefined,
      })
      setAccount(a)
      setCurrent('')
      setNext('')
      setRepeat('')
      toast(next ? 'Saved. Every other device was signed out.' : 'Saved')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    await api.post('/api/logout').catch(() => undefined)
    location.reload()
  }

  async function signOutOthers() {
    try {
      const r = await api.post<{ ended: number }>('/api/account/logout-all')
      toast(`Signed out ${r.ended} other device${r.ended === 1 ? '' : 's'}`)
    } catch (err) {
      toast((err as Error).message)
    }
  }

  return (
    <section class="panel" data-testid="account">
      <h2>Account</h2>
      <p class="small">
        {auth.mac
          ? 'This Mac is always trusted. The username and password are for your other devices.'
          : auth.signedIn
            ? `Signed in as ${account.username}.`
            : 'This device is paired on the Wi-Fi.'}
      </p>
      {!account.passwordSet ? (
        <p class="note warn small">
          No password yet: nobody can sign in from another device or over the internet until you set one.
        </p>
      ) : null}
      {needCurrent && !account.passwordSet ? null : (
        <form onSubmit={save} data-testid="account-form">
          <label class="field">
            Username
            <input
              type="text"
              autoComplete="username"
              autoCapitalize="off"
              spellcheck={false}
              required
              value={username}
              onInput={(e) => setUsername((e.target as HTMLInputElement).value)}
            />
          </label>
          {needCurrent ? (
            <label class="field">
              Current password
              <input
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onInput={(e) => setCurrent((e.target as HTMLInputElement).value)}
              />
            </label>
          ) : null}
          <label class="field">
            {account.passwordSet ? 'New password' : 'Password'} <span class="muted tiny">(at least 8 characters)</span>
            <input
              type="password"
              autoComplete="new-password"
              minLength={8}
              required={!account.passwordSet}
              value={next}
              onInput={(e) => setNext((e.target as HTMLInputElement).value)}
            />
          </label>
          <label class="field">
            Repeat password
            <input
              type="password"
              autoComplete="new-password"
              required={next.length > 0}
              aria-invalid={mismatch ? 'true' : undefined}
              value={repeat}
              onInput={(e) => setRepeat((e.target as HTMLInputElement).value)}
            />
          </label>
          {error ? (
            <p class="note error small" role="alert">
              {error}
            </p>
          ) : null}
          <button class="btn primary" type="submit" disabled={busy || mismatch}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </form>
      )}
      {account.passwordSet ? (
        <div class="row account-actions">
          {auth.signedIn ? (
            <button class="btn" onClick={signOut}>
              Sign out
            </button>
          ) : null}
          <button class="btn" onClick={signOutOthers}>
            Sign out other devices
          </button>
        </div>
      ) : null}
    </section>
  )
}
