import { create } from 'zustand'

export interface Toast {
  id: number
  level: string
  message: string
}

interface AlertState {
  toasts: Toast[]
  push: (level: string, message: string) => void
  dismiss: (id: number) => void
}

let nextId = 1

export const useAlertStore = create<AlertState>((set) => ({
  toasts: [],
  push: (level, message) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts, { id, level, message }] }))
    // auto-dismiss after 5s
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter(t => t.id !== id) })), 5000)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter(t => t.id !== id) })),
}))
