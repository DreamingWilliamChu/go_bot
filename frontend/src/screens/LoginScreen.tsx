import { useState } from 'preact/hooks'
import { api, type AuthInfo } from '../api/client'

interface Props {
  info: AuthInfo
  onSignedIn: () => void
  toast: (m: string) => void
}

export function LoginScreen({ info, onSignedIn, toast }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: Event) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/login', { username: username.trim(), password })
      setPassword('')
      onSignedIn()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div class="screen login-screen">
      <section class="panel">
        <h2>Sign in</h2>
        {info.passwordSet ? null : (
          <p class="note warn small">
            No sign-in password has been set yet. On the Mac, open <code>http://localhost:8000</code>, go to{' '}
            <b>Engine › Account</b> and choose one.
          </p>
        )}
        <form onSubmit={submit} data-testid="login-form">
          <label class="field">
            Username
            <input
              type="text"
              name="username"
              autoComplete="username"
              autoCapitalize="off"
              autoCorrect="off"
              spellcheck={false}
              required
              value={username}
              onInput={(e) => setUsername((e.target as HTMLInputElement).value)}
            />
          </label>
          <label class="field">
            Password
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              aria-invalid={error ? 'true' : undefined}
              onInput={(e) => setPassword((e.target as HTMLInputElement).value)}
            />
          </label>
          {error ? (
            <p class="note error small" role="alert">
              {error}
            </p>
          ) : null}
          <button class="btn primary big" type="submit" disabled={busy || !info.passwordSet}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </section>

      {info.pairing ? (
        <section class="panel">
          <h3>On the same Wi-Fi as the Mac?</h3>
          <p class="small">
            You can also scan the pairing QR code shown in the Mac's terminal (or under <b>Engine</b> on the Mac), or
            type its pairing code:
          </p>
          <PairForm onPaired={onSignedIn} toast={toast} />
        </section>
      ) : null}
    </div>
  )
}

function PairForm({ onPaired, toast }: { onPaired: () => void; toast: (m: string) => void }) {
  const [code, setCode] = useState('')
  return (
    <form
      class="row pairing"
      onSubmit={(e) => {
        e.preventDefault()
        api
          .post('/api/pair', { token: code.trim() })
          .then(() => {
            toast('Paired with the Mac')
            onPaired()
          })
          .catch((err) => toast((err as Error).message))
      }}
    >
      <input
        type="text"
        placeholder="Pairing code"
        value={code}
        autoComplete="off"
        autoCapitalize="off"
        onInput={(e) => setCode((e.target as HTMLInputElement).value)}
      />
      <button class="btn" type="submit">
        Pair
      </button>
    </form>
  )
}
