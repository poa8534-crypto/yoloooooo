import { useMemo } from 'react'
import { layout, type BuildGraph, type GraphNode, type NodeState } from './api'

// The architecture, laid out by dependency depth: a system sits one column to
// the right of everything it needs, so left-to-right IS build order and an edge
// never points backwards.
//
// Every node's state comes from the backend. Nothing here animates progress it
// has inferred -- if the build is not running, nothing pulses, because a pulsing
// node while nothing is happening is a lie told smoothly.

const COLUMN = 220
const ROW = 104
const NODE_WIDTH = 176
const NODE_HEIGHT = 64

const STATE_LABEL: Record<NodeState, string> = {
  built: 'Built', building: 'Building', waiting: 'Waiting',
  refused: 'Refused by the gate', error: 'Error',
}

export function ArchitectureMap({ graph, selected, onSelect }: {
  graph: BuildGraph
  selected: string | null
  onSelect: (node: GraphNode) => void
}) {
  const placed = useMemo(() => layout(graph.nodes), [graph.nodes])
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>()
    placed.forEach(node => {
      map.set(node.id, { x: node.column * COLUMN + 24, y: node.row * ROW + 24 })
    })
    return map
  }, [placed])

  const width = Math.max(...placed.map(node => node.column), 0) * COLUMN + NODE_WIDTH + 64
  const height = Math.max(...placed.map(node => node.row), 0) * ROW + NODE_HEIGHT + 64

  return (
    <div className="architecture-map" data-testid="architecture-map">
      <svg width={width} height={height} role="img"
        aria-label={`${graph.nodes.length} systems and their dependencies`}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(90,110,150,0.45)" />
          </marker>
        </defs>

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
              <text x={14} y={25} className="map-node-name">{node.name}</text>
              <text x={14} y={44} className="map-node-meta">
                {node.layer} · {STATE_LABEL[node.state]}
              </text>
              {node.state === 'built' && <text x={NODE_WIDTH - 20} y={25} className="map-tick">✓</text>}
              {node.state === 'refused' && <text x={NODE_WIDTH - 20} y={25} className="map-warn">!</text>}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
