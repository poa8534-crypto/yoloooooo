import { useMemo, useState } from 'react'
import { clusters, type BuildGraph, type GraphNode, type NodeState } from './api'

// The architecture, laid out by dependency depth: a system sits one column to
// the right of everything it needs, so left-to-right IS build order and an edge
// never points backwards. Rows are banded by layer, because "which of these run
// on the server" is the second question everyone asks after "what is left".
//
// Every node's state comes from the backend. Nothing here animates progress it
// has inferred -- if the build is not running, nothing pulses, because a pulsing
// node while nothing is happening is a lie told smoothly.

const COLUMN = 232
const ROW = 96
const NODE_WIDTH = 188
const NODE_HEIGHT = 66
const PAD = 28
const BAND_LABEL = 22

const STATE_LABEL: Record<NodeState, string> = {
  built: 'Built', building: 'Building', waiting: 'Waiting',
  existing: 'Already in the project', refused: 'Refused by the gate', error: 'Error',
}

const LAYER_LABEL: Record<string, string> = {
  server: 'Server cluster', client: 'Client cluster', shared: 'Shared cluster',
}

export function ArchitectureMap({ graph, selected, onSelect }: {
  graph: BuildGraph
  selected: string | null
  onSelect: (node: GraphNode) => void
}) {
  const [scale, setScale] = useState(1)
  const { placed, bands } = useMemo(() => clusters(graph.nodes), [graph.nodes])

  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>()
    placed.forEach(node => {
      map.set(node.id, { x: node.column * COLUMN + PAD, y: node.row * ROW + PAD + BAND_LABEL })
    })
    return map
  }, [placed])

  const columns = Math.max(...placed.map(node => node.column), 0) + 1
  const rows = Math.max(...placed.map(node => node.row), 0) + 1
  const width = (columns - 1) * COLUMN + NODE_WIDTH + PAD * 2
  const height = (rows - 1) * ROW + NODE_HEIGHT + PAD * 2 + BAND_LABEL

  return (
    <div className="architecture-map" data-testid="architecture-map">
      <div className="map-tools">
        <button type="button" aria-label="Zoom out"
          onClick={() => setScale(value => Math.max(0.5, +(value - 0.15).toFixed(2)))}>−</button>
        <button type="button" aria-label="Reset zoom" onClick={() => setScale(1)}>
          {Math.round(scale * 100)}%
        </button>
        <button type="button" aria-label="Zoom in"
          onClick={() => setScale(value => Math.min(1.6, +(value + 0.15).toFixed(2)))}>+</button>
      </div>

      <div className="map-canvas">
        <svg width={width * scale} height={height * scale}
          viewBox={`0 0 ${width} ${height}`} role="img"
          aria-label={`${graph.nodes.length} systems and their dependencies`}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(88,104,140,0.5)" />
            </marker>
          </defs>

          {/* One band per layer present in the specification. Not a theme: the
              layer decides where the code runs and what it may be trusted with. */}
          {bands.map(band => {
            const top = band.from * ROW + PAD
            const tall = (band.to - band.from) * ROW + NODE_HEIGHT + BAND_LABEL + 12
            return (
              <g key={band.layer} className="map-band" data-layer={band.layer}>
                <rect x={10} y={top} width={width - 20} height={tall} rx={12} />
                <text x={24} y={top + 15} className="map-band-label">
                  {(LAYER_LABEL[band.layer] ?? band.layer).toUpperCase()} · {band.count}
                </text>
              </g>
            )
          })}

          {graph.edges.map(edge => {
            const from = positions.get(edge.from)
            const to = positions.get(edge.to)
            if (!from || !to) return null
            const x1 = from.x + NODE_WIDTH
            const y1 = from.y + NODE_HEIGHT / 2
            const x2 = to.x
            const y2 = to.y + NODE_HEIGHT / 2
            const midpoint = (x1 + x2) / 2
            return (
              <path key={`${edge.from}-${edge.to}`}
                d={`M ${x1} ${y1} C ${midpoint} ${y1}, ${midpoint} ${y2}, ${x2} ${y2}`}
                className="map-edge" markerEnd="url(#arrow)" fill="none" />
            )
          })}

          {placed.map(node => {
            const position = positions.get(node.id)
            if (!position) return null
            const isSelected = selected === node.id
            return (
              <g key={node.id} transform={`translate(${position.x}, ${position.y})`}
                className="map-node" data-state={node.state} data-selected={isSelected}
                onClick={() => onSelect(node)} role="button" tabIndex={0}
                aria-label={`${node.name}, ${STATE_LABEL[node.state]}`}
                onKeyDown={event => { if (event.key === 'Enter') onSelect(node) }}>
                <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx={10} />
                <text x={15} y={26} className="map-node-name">{node.name}</text>
                <text x={15} y={45} className="map-node-meta">{STATE_LABEL[node.state]}</text>
                {node.state === 'built' &&
                  <text x={NODE_WIDTH - 22} y={26} className="map-tick">✓</text>}
                {(node.state === 'refused' || node.state === 'error') &&
                  <text x={NODE_WIDTH - 22} y={26} className="map-warn">!</text>}
                {node.attempts > 1 && (
                  <text x={NODE_WIDTH - 15} y={45} className="map-node-attempts"
                    textAnchor="end">{node.attempts} attempts</text>
                )}
              </g>
            )
          })}
        </svg>
      </div>

      <div className="map-legend">
        <span data-state="built">Built</span>
        <span data-state="building">Building</span>
        <span data-state="waiting">Waiting</span>
        <span data-state="existing">Already there</span>
        <span data-state="refused">Refused</span>
        <span className="map-legend-note">
          Left to right is build order · spec revision {graph.spec_revision} · {graph.content_hash}
        </span>
      </div>
    </div>
  )
}
