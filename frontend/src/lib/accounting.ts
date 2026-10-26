/**
 * Client-side mirror of `backend/privacy/accounting.py`. Used only for display:
 * the server is the authority on every charge that actually hits the ledger.
 */

export const DEFAULT_DELTA = 1e-6

/** The classic Bun-Steinke conversion: valid but not tight. */
function epsFromRhoClassic(rho: number, delta: number): number {
  return rho + 2 * Math.sqrt(rho * Math.log(1 / delta))
}

/** eps for one Renyi order a > 1 (Canonne-Kamath-Steinke 2020, Prop. 1.3); valid for every a. */
function epsAtOrder(rho: number, logDelta: number, alpha: number): number {
  const a1 = alpha - 1
  return alpha * rho - (logDelta + Math.log(a1) - alpha * Math.log(1 - 1 / alpha)) / a1
}

/**
 * Tightest (eps, delta) equivalent of a zCDP spend we can prove, never above the classic bound.
 * Mirrors `eps_from_rho` in backend/privacy/accounting.py.
 */
export function epsFromRho(rho: number, delta = DEFAULT_DELTA): number {
  if (rho <= 0) return 0
  const logDelta = Math.log(delta)
  let best = epsFromRhoClassic(rho, delta)
  let bestU = 1
  for (let k = -100; k <= 140; k++) {
    const u = 10 ** (k / 20)
    const e = epsAtOrder(rho, logDelta, 1 + u)
    if (e < best) {
      best = e
      bestU = u
    }
  }
  const phi = (Math.sqrt(5) - 1) / 2
  let lo = bestU / 10 ** 0.05
  let hi = bestU * 10 ** 0.05
  for (let i = 0; i < 60; i++) {
    const a = hi - phi * (hi - lo)
    const b = lo + phi * (hi - lo)
    if (epsAtOrder(rho, logDelta, 1 + a) < epsAtOrder(rho, logDelta, 1 + b)) hi = b
    else lo = a
  }
  return Math.max(0, Math.min(best, epsAtOrder(rho, logDelta, 1 + (lo + hi) / 2)))
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
