import {
  ChartLineUp,
  Database,
  FileText,
  Flask,
  Gauge,
  IdentificationBadge,
  UsersThree,
  HeartStraight,
  ListChecks,
  ShieldCheck,
  Stethoscope,
  Table,
  Terminal,
  TestTube,
  type Icon,
} from '@phosphor-icons/react'

import { GATEWAY_MODE } from '../../lib/backend'
import type { RoleName } from '../../lib/types'

export interface NavItem {
  to: string
  label: string
  icon: Icon
  /** Roles that see the entry. dba passes every gate (see stores/auth). */
  roles: RoleName[]
  end?: boolean
  /** Served only by the legacy Django API; hidden in gateway mode. */
  legacy?: boolean
  /** Served only by the gateway; hidden in legacy mode. */
  gatewayOnly?: boolean
}

export interface NavGroup {
  label: string | null
  items: NavItem[]
}

const ALL: RoleName[] = ['dp_analyst', 'clinician', 'auditor', 'dba']

const ALL_GROUPS: NavGroup[] = [
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
      { to: '/data/patients', label: 'Patients', icon: IdentificationBadge, roles: ['clinician'], legacy: true },
      { to: '/data/visits', label: 'Visits', icon: Table, roles: ['clinician'], legacy: true },
      { to: '/data/diagnoses', label: 'Diagnoses', icon: Stethoscope, roles: ['clinician'], legacy: true },
      { to: '/data/labs', label: 'Lab results', icon: TestTube, roles: ['clinician'], legacy: true },
    ],
  },
  {
    label: 'Governance',
    items: [
      { to: '/governance/policies', label: 'Policies', icon: ListChecks, roles: ['dba'], legacy: true },
      { to: '/governance/grants', label: 'Column grants', icon: Database, roles: ['dba'], legacy: true },
      { to: '/governance/users', label: 'Users', icon: UsersThree, roles: ['dba'], legacy: true },
      { to: '/governance/roles', label: 'Roles', icon: IdentificationBadge, roles: ['dba'], legacy: true },
      { to: '/governance/grid', label: 'Policy grid', icon: ListChecks, roles: ['dba', 'auditor'], gatewayOnly: true },
      { to: '/governance/audit', label: 'Audit', icon: FileText, roles: ['auditor', 'dp_analyst'] },
      { to: '/governance/ledger', label: 'Ledger', icon: ShieldCheck, roles: ['auditor'] },
      { to: '/governance/patients', label: 'Patient budgets', icon: HeartStraight, roles: ['dba'], legacy: true },
    ],
  },
  {
    label: null,
    items: [
      { to: '/attack-lab', label: 'Attack Lab', icon: Flask, roles: ['dp_analyst', 'auditor'] },
    ],
  },
]

export const NAV: NavGroup[] = ALL_GROUPS.map((group) => ({
  ...group,
  items: group.items.filter((item) => (GATEWAY_MODE ? !item.legacy : !item.gatewayOnly)),
})).filter((group) => group.items.length > 0)

/** Flat lookup used by the topbar to name the current section. */
export const NAV_INDEX: NavItem[] = NAV.flatMap((group) => group.items)

export const SECTION_ICON = ChartLineUp
