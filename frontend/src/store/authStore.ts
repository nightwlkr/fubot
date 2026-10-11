import { create } from 'zustand'
import { api } from '../api/client'
import { ENDPOINTS } from '../api/endpoints'
import Cookies from 'js-cookie'
import { wsManager } from '../api/websocket'

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
  token: Cookies.get('fubot_token') || null,
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
      Cookies.set('fubot_token', token, { expires: 7 })
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
    Cookies.remove('fubot_token')
    wsManager.disconnect()
    set({ user: null, token: null })
  },

  checkAuth: async () => {
    const token = Cookies.get('fubot_token')
    if (!token) {
      set({ user: null, token: null, isRestoring: false })
      return
    }
    try {
      const res = await api.get<{ user: User }>(ENDPOINTS.me)
      set({ user: res.data.user, token, isRestoring: false })
      wsManager.connect()
    } catch {
      Cookies.remove('fubot_token')
      set({ user: null, token: null, isRestoring: false })
    }
  },
}))
