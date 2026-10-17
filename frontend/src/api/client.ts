import axios from 'axios'
import { API_BASE, ENDPOINTS } from './endpoints'
import { getToken, clearToken } from './token'

export const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

// Attach JWT token to every request
apiClient.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 → clear token and redirect to login
apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status
    // Only treat a 401 on a PROTECTED endpoint as "session ended". A 401 from
    // /auth/login or /auth/me itself is a normal signal handled by the caller
    // (login failure / checkAuth) — a hard redirect here would bounce a
    // successful restore straight back to /login.
    const url: string = err.config?.url ?? ''
    const isAuthEndpoint = url.includes('/auth/login') || url.includes('/auth/me')
    if (status === 401 && !isAuthEndpoint) {
      clearToken()
      window.location.href = '/login'
    } else if (status === 429) {
      console.warn('Rate limited')
    } else if (!err.response) {
      console.error('Network error')
    }
    return Promise.reject(err)
  }
)

// Typed helpers.
//
// `get` takes the QUERY PARAMS DIRECTLY — not an axios config.
//     api.get(ENDPOINTS.trades, { side: 'SHORT' })   ✅ → ?side=SHORT
//     api.get(ENDPOINTS.trades, { params: {...} })   ❌ → ?params[side]=SHORT
//
// The second form is the mistake that silently broke every filter on the Trade
// Log, Scanner and Performance pages: axios serialises the nested object into
// `params[key]=value`, the backend sees no `side` at all, and the page quietly
// returns every row with a 200. Guard against a regression because it is
// invisible at runtime — the request succeeds, it just asks the wrong question.
function queryParams(params?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!params) return undefined
  if (Object.keys(params).length === 1 && 'params' in params) {
    console.error(
      `[api.get] ${JSON.stringify(params)} looks like an axios config, not query ` +
      `params. Pass the params directly — api.get(url, { side: 'SHORT' }). ` +
      `Wrapping them in { params } sends "params[side]=SHORT" and every filter ` +
      `is silently ignored.`)
    return params.params as Record<string, unknown>
  }
  return params
}

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) =>
    apiClient.get<T>(url, { params: queryParams(params) }),

  post: <T>(url: string, data?: unknown) =>
    apiClient.post<T>(url, data),

  patch: <T>(url: string, data?: unknown) =>
    apiClient.patch<T>(url, data),

  put: <T>(url: string, data?: unknown) =>
    apiClient.put<T>(url, data),

  delete: <T>(url: string) =>
    apiClient.delete<T>(url),
}

/**
 * Extract a human-readable error message from a failed request.
 *
 * Axios rejects with `unknown`, so every catch site needs a cast to reach the
 * backend's `detail`. Centralised here so no page re-invents it (and so a single
 * place satisfies strict catch-clause checks).
 */
export function errorDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return detail ? String(detail) : fallback
}
