import type { ReactElement } from 'react'
import type { ExplorerRow, SyncState } from './api'

// The DataModel tree this build maps to, built by the backend from the same
// path mapping the sync uses.
//
// The header is the honest part. A tree like this reads as though it were
// Studio's own explorer, so it says plainly whether anything has been sent:
// "not sent yet" is a different state from "sent and applied", and only the
// build record can tell them apart.

function rowsOf(rows: ExplorerRow[], depth = 0, prefix = ''): ReactElement[] {
  return rows.flatMap(row => [
    // Keyed by full path. Depth and name alone collide: ReplicatedStorage/Shared
    // and ServerScriptService/Server can each hold a module of the same name at
    // the same depth, and React would see one key twice.
    <li key={`${prefix}/${row.name}`} data-depth={depth} data-state={row.state || undefined}
      data-kind={row.children.length ? 'folder' : 'script'}>
      <span className="explorer-icon" aria-hidden="true">
        {row.children.length ? '▸' : row.class === 'Script' ? '⬢' : '◧'}
      </span>
      <span className="explorer-name">{row.name}</span>
      {row.state && <span className="explorer-state" data-state={row.state}>{row.state}</span>}
    </li>,
    ...rowsOf(row.children, depth + 1, `${prefix}/${row.name}`),
  ])
}

export function StudioExplorer({ explorer, sync }: { explorer: ExplorerRow[]; sync: SyncState }) {
  const sent = Boolean(sync.batch_id)
  const reported = sync.applied != null

  return (
    <section className="studio-explorer panel" aria-label="Roblox Studio explorer">
      <header className="panel-head">
        <span className="micro-label">Roblox Studio explorer</span>
        <span className="sync-pill" data-sent={sent} data-reported={reported}>
          {!sent ? 'Not sent yet'
            : !reported ? `${sync.operations} queued`
              : sync.failed ? `${sync.failed} failed`
                : `${sync.applied} applied`}
        </span>
      </header>

      {!sent && (
        <p className="explorer-note">
          This is where the build will put these systems. Nothing has been sent to Studio
          for this build yet.
        </p>
      )}
      {sent && reported && (
        <p className="explorer-note">
          {sync.applied} applied · {sync.skipped} unchanged · {sync.failed} failed
          {sync.reported_at ? ` at ${sync.reported_at.slice(11, 19)}` : ''}
        </p>
      )}

      <ul className="explorer-tree">{rowsOf(explorer)}</ul>
      {explorer.length === 0 && <p className="explorer-note">No system maps into the DataModel.</p>}
    </section>
  )
}
