import { useEffect, useRef, useState } from 'preact/hooks'

interface Props {
  onCapture: (blob: Blob) => void
  onClose: () => void
}

/** Live camera preview (needs a secure context: HTTPS or localhost). */
export function LiveCamera({ onCapture, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    navigator.mediaDevices
      .getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 } },
        audio: false,
      })
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = s
        if (videoRef.current) {
          videoRef.current.srcObject = s
          void videoRef.current.play()
        }
      })
      .catch((e: Error) => setError(`Camera unavailable: ${e.message}. Use "Take photo" instead.`))
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  function capture() {
    const v = videoRef.current
    if (!v || !v.videoWidth) return
    const c = document.createElement('canvas')
    c.width = v.videoWidth
    c.height = v.videoHeight
    c.getContext('2d')!.drawImage(v, 0, 0)
    c.toBlob((b) => b && onCapture(b), 'image/jpeg', 0.92)
  }

  return (
    <div class="panel">
      {error ? <p class="note error">{error}</p> : <video ref={videoRef} class="live" playsInline muted />}
      <p class="muted tiny">Fit the whole board in the frame, hold the phone above the board and avoid glare.</p>
      <div class="row">
        <button class="btn primary" onClick={capture} disabled={!!error}>
          Capture
        </button>
        <button class="btn" onClick={onClose}>
          Close camera
        </button>
      </div>
    </div>
  )
}
