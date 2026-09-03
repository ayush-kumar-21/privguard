"""
PrivGuard reference implementation — the "oracle".

This package is an independent, engine-free implementation of the privacy
semantics frozen in `SPEC.md`. Its job is to be *obviously* correct rather than
fast, so that the Rust engine can be differentially tested against it
(`IMPLEMENTATION_PLAN.md` Phase 3.8).

Two rules keep it useful:

1. **No Django, no database, no engine imports.** The oracle answers queries
   over plain Python lists. If it needed the system under test to run, it could
   not be evidence about that system.
2. **Stdlib only.** Every dependency is another way for the reference to
   disagree with itself between two machines.

Submodules are *not* imported eagerly here. Doing so makes
`python -m privguard_oracle.dataset` emit a `runpy` warning about the module
already being in `sys.modules`, and it forces every consumer to pay for the
whole package to use one function. Import what you need:

    from privguard_oracle import accounting, dp
"""

__all__ = ["accounting", "dataset", "dp", "mechanisms", "spec"]
__version__ = "0.1.0"
