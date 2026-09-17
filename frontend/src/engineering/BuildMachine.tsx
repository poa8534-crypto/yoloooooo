import { useEffect, useState } from 'react'
import { engineeringApi, type Toolchain } from './api'

// What the machine that runs builds can actually do.
//
// This page is usually open on a different device from the one doing the work,
// so "is agy installed" is a question the person cannot answer by looking
// around them. Every line here is read from that machine, resolving each tool
// the way the build resolves it -- a Rokit shim on PATH is not proof, because
// the shim is a stub that looks up a pinned version and can fail to find it.
//
// Collapsed to one line while everything is fine. A readiness panel that takes
// up room to say "yes" trains you to stop reading it.

export function BuildMachine() {
  const [toolchain, setToolchain] = useState<Toolchain | null>(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const ask = () => engineeringApi.toolchain()
      .then(answer => { setToolchain(answer); setError('') })
      .catch(caught => setError((caught as Error).message))
    void ask()
    // Slow: a toolchain changes when someone installs something, not while you
    // watch. Each read shells out to five executables.
    const timer = setInterval(ask, 60_000)
    return () => clearInterval(timer)
  }, [])

  const ready = toolchain?.ready ?? false
  const missing = toolchain?.missing ?? []

  return (
    <section className="build-machine" data-ready={ready} data-open={open}
      aria-label="Build machine">
      <button type="button" className="machine-summary" onClick={() => setOpen(value => !value)}
        aria-expanded={open}>
        <span className="micro-label">Build machine</span>
        <strong>
          {error ? 'Cannot be read'
            : !toolchain ? 'Checking…'
              : ready ? 'Ready'
                : `Missing ${missing.join(', ')}`}
        </strong>
        {toolchain && ready && <span className="machine-note">
          {toolchain.tools.length} tools · every one answered</span>}
        <span className="machine-chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
      </button>

      {error && <p className="machine-error">{error}</p>}

      {open && toolchain && (
        <ul className="tool-list">
          {toolchain.tools.map(tool => (
            <li key={tool.name} data-present={tool.present}>
              <span className="tool-mark" aria-hidden="true">{tool.present ? '✓' : '✕'}</span>
              <span className="tool-name">{tool.name}</span>
              <span className="tool-version">{tool.version || (tool.present ? '' : '—')}</span>
              <span className="tool-detail">{tool.detail || tool.purpose}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
