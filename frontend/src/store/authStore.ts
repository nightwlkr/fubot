import { create } from 'zustand'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import { wsManager } from '../api/websocket'
import { getToken, setToken, clearToken } from '../api/token'

export interface User {
  id: number
  username: string
  role: string
}

interface AuthState {
  user: User | null
  token: string | null
  isLoading: boolean
  isRestoring: boolean  // true until the session restore on page load finishes
  error: string | null

  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: getToken(),
  isLoading: false,
  isRestoring: true,  // checked in checkAuth() on app load
  error: null,

  login: async (username, password) => {
    set({ isLoading: true, error: null })
    try {
      const res = await api.post<{ token: string; user: User }>(
        ENDPOINTS.login,
        { username, password }
      )
      const { token, user } = res.data
      setToken(token)
      wsManager.connect()
      set({ user, token, isLoading: false })
    } catch (err: any) {
      set({
        error: err.response?.data?.detail || 'Login failed',
        isLoading: false,
      })
      throw err
    }
  },

  logout: async () => {
    try {
      await api.post(ENDPOINTS.logout)
    } catch {
      // Ignore logout errors
    }
    clearToken()
    wsManager.disconnect()
    set({ user: null, token: null })
  },

  checkAuth: async () => {
    const token = getToken()
    // Always attempt /me even without a stored token: the httpOnly cookie set
    // at login rides along automatically (withCredentials: true) and is enough
    // to authenticate. This covers browsers that hold the cookie but whose
    // localStorage was cleared or predates the durable-token fix.
    try {
      // /me returns a FLAT user object ({ id, username, role, token }) — NOT
      // nested under a `user` key like the login response does. Reading
      // res.data.user here was undefined, so `user` was never restored and
      // every refresh bounced to /login even though auth succeeded.
      const res = await api.get<User & { token?: string }>(ENDPOINTS.me)
      const { token: serverToken, ...user } = res.data
      const effective = token ?? serverToken ?? null
      if (effective) setToken(effective)
      set({ user, token: effective, isRestoring: false })
      wsManager.connect()
    } catch (err: any) {
      // Only clear the stored token on a genuine 401 (expired/invalid). A
      // transient network error should NOT wipe the session — keep the token
      // so the next checkAuth/refresh can restore it.
      if (err.response?.status === 401) {
        clearToken()
        set({ user: null, token: null, isRestoring: false })
      } else {
        // Transient failure (502 during a deploy, network blip, offline): the
        // session is NOT proven invalid, so keep the user and token. Nulling
        // `user` here made RequireAuth redirect to /login — bouncing the
        // operator off the dashboard with live positions open.
        set({ isRestoring: false })
      }
    }
  },
}))
