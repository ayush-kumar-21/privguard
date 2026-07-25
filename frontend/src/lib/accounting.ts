/**
 * Client-side mirror of `backend/privacy/accounting.py`. Used only for display:
 * the server is the authority on every charge that actually hits the ledger.
 */

export const DEFAULT_DELTA = 1e-6

/** (eps, delta) equivalent of a zCDP spend: eps = rho + 2*sqrt(rho*ln(1/delta)). */
export function epsFromRho(rho: number, delta = DEFAULT_DELTA): number {
  if (rho <= 0) return 0
  return rho + 2 * Math.sqrt(rho * Math.log(1 / delta))
}

/** Laplace scale b = Delta1 / sqrt(2*rho). */
export function laplaceScaleFromRho(delta1: number, rho: number): number {
  if (rho <= 0) return Number.POSITIVE_INFINITY
  return delta1 / Math.sqrt(2 * rho)
}

/** Gaussian sigma = Delta2 / sqrt(2*rho). */
export function sigmaFromRho(delta2: number, rho: number): number {
  if (rho <= 0) return Number.POSITIVE_INFINITY
  return delta2 / Math.sqrt(2 * rho)
}
