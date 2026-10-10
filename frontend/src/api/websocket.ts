import { API_BASE } from './endpoints'
import Cookies from 'js-cookie'

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

  connect() {
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws'
    this.ws = new WebSocket(wsUrl)

    this.ws.onopen = () => {
      this.reconnectDelay = 2000
      const token = Cookies.get('fubot_token')
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
        setTimeout(() => {
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
