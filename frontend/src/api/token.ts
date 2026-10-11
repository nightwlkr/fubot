const KEY = 'fubot_token'

/**
 * Token storage helper.
 *
 * The backend sets its own httpOnly `fubot_token` cookie on login — that copy
 * is invisible to JS and is what the backend reads. The frontend keeps its own
 * durable, JS-readable copy in localStorage (the httpOnly cookie can't be read
 * from JS, so the app needs its own store to send the Bearer header and the
 * WebSocket auth message).
 *
 * There is deliberately NO js-cookie duplicate here: a same-name JS cookie
 * goes stale relative to localStorage (clearToken can't remove the httpOnly
 * one, so a later Cookies.get could return an old token while localStorage
 * holds the new one). localStorage is the single source of truth for the
 * frontend; the httpOnly cookie is the backend's.
 */
export function getToken(): string | null {
  return localStorage.getItem(KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(KEY)
}
