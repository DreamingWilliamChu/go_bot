import { useEffect, useState } from 'preact/hooks'
import { api, type ModelInfo, type StatusResponse } from '../api/client'

interface Props {
  status: StatusResponse | null
  refreshStatus: () => void
  toast: (m: string) => void
}

const isLocalhost = () => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)

export function SettingsScreen({ status, refreshStatus, toast }: Props) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [switching, setSwitching] = useState<string | null>(null)
  const [pairInfo, setPairInfo] = useState<{ url: string | null; lan: boolean; https: boolean } | null>(null)

  useEffect(() => {
    api
      .get<{ models: ModelInfo[] }>('/api/models')
      .then((r) => setModels(r.models))
      .catch(() => setModels([]))
    if (isLocalhost())
      api
        .get<{ url: string | null; lan: boolean; https: boolean }>('/api/pair/info')
        .then(setPairInfo)
        .catch(() => setPairInfo(null))
  }, [status?.engine.modelId])

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
        {isLocalhost() ? (
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
    </div>
  )
}
