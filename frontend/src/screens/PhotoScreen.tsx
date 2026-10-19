import type { StatusResponse } from '../api/client'
import type { Setup } from '../game/game'

interface Props {
  defaults: Setup
  status: StatusResponse | null
  toast: (m: string) => void
  onConfirmed: (setup: Setup) => void
}

// Placeholder until the photo pipeline lands (stage 2).
export function PhotoScreen(_p: Props) {
  return (
    <div class="screen">
      <section class="panel">
        <h2>Photo</h2>
        <p>Photo recognition is being added.</p>
      </section>
    </div>
  )
}
