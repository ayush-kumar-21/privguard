"""The role catalogue.

Four base roles carry every permission the application enforces:

    dp_analyst  runs noisy aggregate queries against a privacy budget
    auditor     reads the audit log, the budget ledger and every account
    clinician   reads and updates identified clinical records
    dba         administers policies, users, grants and quotas (superset of the rest in the UI)

Job roles are named for what people do at an enterprise. Each one *carries* exactly one base role
and never more, so assigning `researcher` gives analyst access and nothing else. (A role hierarchy:
the checks stay in one place, `Principal.has_role`, and nobody has to edit twenty permission classes
to add a job title.) There is deliberately no job role that carries `dba`: administrator power is
granted by assigning `dba` itself.
"""

BASE_ROLES = {
    "dp_analyst": "Runs noisy aggregate queries against a fixed privacy budget.",
    "auditor": "Reads the query log, the budget ledger and every account.",
    "clinician": "Reads and updates identified clinical records.",
    "dba": "Administers users, roles, grants, policies and quotas.",
}

# job role -> (base role it carries, description)
JOB_ROLES = {
    # analysis: carry dp_analyst
    "data_scientist": ("dp_analyst", "Builds and tests models through DP queries."),
    "researcher": ("dp_analyst", "Studies the data through DP queries under a fixed budget."),
    "biostatistician": ("dp_analyst", "Statistical analysis of clinical outcomes through DP queries."),
    "epidemiologist": ("dp_analyst", "Population-level rates and trends through DP queries."),
    "business_analyst": ("dp_analyst", "Operational and financial reporting through DP queries."),
    # oversight: carry auditor
    "compliance_officer": ("auditor", "Checks that data use follows policy; reads the audit log and ledger."),
    "privacy_officer": ("auditor", "Data protection officer: watches budgets and every query."),
    "internal_auditor": ("auditor", "Independent review of queries, budgets and the ledger."),
    "security_auditor": ("auditor", "Reviews access and the integrity of the budget ledger."),
    # clinical: carry clinician
    "physician": ("clinician", "Reads and updates identified patient records."),
    "nurse": ("clinician", "Reads and updates identified patient records."),
}


# The base roles that run DP queries. A principal may query only if its effective roles include one.
QUERY_ROLES = frozenset({"dp_analyst", "auditor"})


def carried_role(name: str) -> str | None:
    """The base role a job role carries, or None for a base role / unknown name."""
    entry = JOB_ROLES.get(name)
    return entry[0] if entry else None


def effective_role_names(assigned) -> set[str]:
    """Assigned role names plus the base roles they carry. This is what permission checks use."""
    names = set(assigned)  # materialise: `assigned` may be a one-shot iterator
    names.update(b for r in list(names) if (b := carried_role(r)))
    return names
