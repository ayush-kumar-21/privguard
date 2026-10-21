import '@xyflow/react/dist/style.css'

import {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { useMemo } from 'react'

import { cn } from '../../lib/cn'
import type { PlanExplain } from '../../lib/types'
import { useUI } from '../../stores/ui'

/** What each physical operator does, in the vocabulary of `SPEC.md` §4. */
const DESCRIPTION: Record<string, string> = {
  Scan: 'read the visits heap file',
  Filter: 'keep rows matching WHERE',
  ContributionBound: 'at most C rows and G groups per patient',
  Clamp: 'clip values into the policy bounds',
  PartitionSelect: 'drop groups with too few patients',
  DPAggregate: 'aggregate, then add calibrated noise',
}

/** Operators that exist to make the answer private; the rest are ordinary query execution. */
const PRIVACY_OPERATORS = new Set(['ContributionBound', 'Clamp', 'PartitionSelect', 'DPAggregate'])

type OperatorData = { name: string; detail: string; step: number; private: boolean }

function OperatorNode({ data }: NodeProps<Node<OperatorData>>) {
  return (
    <div
      className={cn(
        'w-[168px] rounded-md border px-3 py-2.5 text-left',
        data.private
          ? 'border-accent bg-accent-weak text-text'
          : 'border-border-strong bg-surface text-text',
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-faint" />
      <div className="flex items-baseline gap-2">
        <span className="num text-micro text-faint">{data.step}</span>
        <span className="text-small font-medium">{data.name}</span>
      </div>
      <p className="mt-1 text-micro leading-snug text-muted">{data.detail}</p>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-faint" />
    </div>
  )
}

const nodeTypes = { operator: OperatorNode }

const STEP = 208

/**
 * The physical plan as a flow of operators, left to right in execution order. The accent-tinted
 * nodes are the privacy operators: they sit inside the plan, between the scan and the answer,
 * which is the point of building privacy into the executor rather than around it.
 */
export default function PlanViewer({ plan }: { plan: PlanExplain }) {
  const theme = useUI((state) => state.theme)

  const { nodes, edges } = useMemo(() => {
    const nodes: Node<OperatorData>[] = plan.physical.map((name, i) => ({
      id: `${i}`,
      type: 'operator',
      position: { x: i * STEP, y: 0 },
      draggable: false,
      data: {
        name,
        step: i + 1,
        private: PRIVACY_OPERATORS.has(name),
        detail:
          name === 'DPAggregate'
            ? `aggregate + ${plan.mechanism.replace('_', ' ')} noise`
            : (DESCRIPTION[name] ?? ''),
      },
    }))
    const edges: Edge[] = nodes.slice(1).map((_, i) => ({
      id: `e${i}`,
      source: `${i}`,
      target: `${i + 1}`,
      markerEnd: { type: MarkerType.ArrowClosed },
    }))
    return { nodes, edges }
  }, [plan])

  return (
    <div
      className="h-[150px] w-full"
      role="img"
      aria-label={`Query plan: ${plan.physical.join(', then ')}`}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnScroll={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      </ReactFlow>
    </div>
  )
}
