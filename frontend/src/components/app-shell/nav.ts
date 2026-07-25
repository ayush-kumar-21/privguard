import {
  ChartLineUp,
  Database,
  FileText,
  Flask,
  Gauge,
  IdentificationBadge,
  ListChecks,
  ShieldCheck,
  Stethoscope,
  Table,
  Terminal,
  TestTube,
  type Icon,
} from '@phosphor-icons/react'

import type { RoleName } from '../../lib/types'

export interface NavItem {
  to: string
  label: string
  icon: Icon
  /** Roles that see the entry. dba passes every gate (see stores/auth). */
  roles: RoleName[]
  end?: boolean
}

export interface NavGroup {
  label: string | null
  items: NavItem[]
}

const ALL: RoleName[] = ['dp_analyst', 'clinician', 'auditor', 'dba']

export const NAV: NavGroup[] = [
  {
    label: null,
    items: [
      { to: '/', label: 'Overview', icon: Gauge, roles: ALL, end: true },
      { to: '/console', label: 'Console', icon: Terminal, roles: ['dp_analyst', 'auditor'] },
    ],
  },
  {
    label: 'Data',
    items: [
      { to: '/data/patients', label: 'Patients', icon: IdentificationBadge, roles: ['clinician'] },
      { to: '/data/visits', label: 'Visits', icon: Table, roles: ['clinician'] },
      { to: '/data/diagnoses', label: 'Diagnoses', icon: Stethoscope, roles: ['clinician'] },
      { to: '/data/labs', label: 'Lab results', icon: TestTube, roles: ['clinician'] },
    ],
  },
  {
    label: 'Governance',
    items: [
      { to: '/governance/policies', label: 'Policies', icon: ListChecks, roles: ['dba'] },
      { to: '/governance/grants', label: 'Column grants', icon: Database, roles: ['dba'] },
      { to: '/governance/roles', label: 'Roles', icon: IdentificationBadge, roles: ['dba'] },
      { to: '/governance/audit', label: 'Audit', icon: FileText, roles: ['auditor', 'dp_analyst'] },
      { to: '/governance/ledger', label: 'Ledger', icon: ShieldCheck, roles: ['auditor'] },
    ],
  },
  {
    label: null,
    items: [
      { to: '/attack-lab', label: 'Attack Lab', icon: Flask, roles: ['dp_analyst', 'auditor'] },
    ],
  },
]

/** Flat lookup used by the topbar to name the current section. */
export const NAV_INDEX: NavItem[] = NAV.flatMap((group) => group.items)

export const SECTION_ICON = ChartLineUp
