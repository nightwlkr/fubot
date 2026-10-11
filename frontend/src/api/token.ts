import Cookies from 'js-cookie'

const KEY = 'fubot_token'

/**
 * Token storage helper.
 *
 * The backend sets its own httpOnly fubot_token cookie on login — browsers
 * silently REJECT a JS write with the same name, so a JS-cookie copy never
 * survives on production. localStorage is the durable JS-readable copy:
 * needed by the Bearer header and the WebSocket auth message.
 */
export function getToken(): string | null {
  return Cookies.get(KEY) || localStorage.getItem(KEY)
}

export function setToken(token: string): void {
  Cookies.set(KEY, token, { expires: 7 })
  localStorage.setItem(KEY, token)
}

export function clearToken(): void {
  Cookies.remove(KEY)
  localStorage.removeItem(KEY)
}
