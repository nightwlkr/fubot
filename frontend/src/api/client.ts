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

// Typed helpers
export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) =>
    apiClient.get<T>(url, { params }),

  post: <T>(url: string, data?: unknown) =>
    apiClient.post<T>(url, data),

  patch: <T>(url: string, data?: unknown) =>
    apiClient.patch<T>(url, data),

  put: <T>(url: string, data?: unknown) =>
    apiClient.put<T>(url, data),

  delete: <T>(url: string) =>
    apiClient.delete<T>(url),
}
