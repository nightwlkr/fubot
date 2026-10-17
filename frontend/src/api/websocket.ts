import { API_BASE } from './endpoints'
import { getToken } from './token'

export type WSType =
  | 'auth'
  | 'position_update'
  | 'order_update'
  | 'signal_update'
  | 'pnl_update'
  | 'bot_status'
  | 'alert'

export interface WSMessage {
  type: WSType
  data?: unknown
}

type WSHandler = (msg: WSMessage) => void

class WebSocketManager {
  private ws: WebSocket | null = null
  private handlers: Set<WSHandler> = new Set()
  private reconnectDelay = 2000
  private maxReconnectDelay = 30000
  private shouldReconnect = true
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  connect() {
    // disconnect() (logout) sets shouldReconnect = false. Resetting it here is
    // what makes the socket survive a logout → login cycle: without the reset,
    // the onclose handler below sees false and never reconnects, so alerts and
    // push updates die for the rest of the tab.
    this.shouldReconnect = true

    // Never stack sockets. connect() is called on every login, every session
    // restore, and by the reconnect chain itself; a second live socket would
    // keep its own onclose → connect() chain running (zombie) and double every
    // broadcast. Reuse the live one instead.
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    // A closed/closing socket is safe to replace — but drop its handlers first
    // so its onclose cannot schedule another reconnect after we reassign.
    if (this.ws) {
      this.ws.onclose = null
      this.ws.onerror = null
      this.ws.close()
      this.ws = null
    }

    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws'
    this.ws = new WebSocket(wsUrl)

    this.ws.onopen = () => {
      this.reconnectDelay = 2000
      const token = getToken()
      if (token) {
        this.send({ type: 'auth', data: { token } })
      }
    }

    this.ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        this.handlers.forEach((h) => h(msg))
      } catch {
        // Ignore parse errors
      }
    }

    this.ws.onclose = () => {
      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => {
          this.reconnectDelay = Math.min(
            this.reconnectDelay * 1.5,
            this.maxReconnectDelay
          )
          this.connect()
        }, this.reconnectDelay)
      }
    }

    this.ws.onerror = () => {
      // onclose will handle reconnection
    }
  }

  disconnect() {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
  }

  send(msg: WSMessage) {
    this.ws?.send(JSON.stringify(msg))
  }

  onMessage(handler: WSHandler) {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }
}

export const wsManager = new WebSocketManager()
