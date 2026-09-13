// `??`, nao `||`: definida como VAZIA (producao, mesmo dominio) produz chamada
// relativa `/api/v1/...`; AUSENTE (desenvolvimento) cai no padrao local. Com
// `||`, vazia e ausente seriam a mesma coisa e producao chamaria localhost.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = "ApiError"
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("access_token")
}

export function getActiveSellerId(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("active_seller_id")
}

export function clearAuth(): void {
  if (typeof window === "undefined") return
  localStorage.removeItem("access_token")
  localStorage.removeItem("refresh_token")
  localStorage.removeItem("active_seller_id")
}

// Uma única Promise em voo por vez — requests simultâneos reutilizam o mesmo refresh
let _refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise
  _refreshPromise = (async (): Promise<string | null> => {
    try {
      const rt = typeof window !== "undefined" ? localStorage.getItem("refresh_token") : null
      if (!rt) return null
      const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      })
      if (!res.ok) return null
      const data = await res.json()
      if (typeof window !== "undefined") {
        localStorage.setItem("access_token", data.access_token)
        localStorage.setItem("refresh_token", data.refresh_token) // rotação
      }
      return data.access_token as string
    } catch {
      return null
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}

function forceLogin(): never {
  clearAuth()
  if (typeof window !== "undefined") window.location.href = "/login"
  throw new ApiError(401, "Sessão expirada. Faça login novamente.")
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new ApiError(res.status, data?.detail || "Erro desconhecido")
  return data as T
}

/**
 * Executa doFetch(token), e se retornar 401 tenta renovar o access token
 * silenciosamente e repete a chamada uma vez. Se o refresh falhar ou a
 * segunda tentativa também retornar 401, redireciona para /login.
 *
 * Exportado para módulos que constroem o fetch manualmente (ex: uploads multipart).
 */
export async function withRefresh(
  doFetch: (token: string | null) => Promise<Response>
): Promise<Response> {
  const res = await doFetch(getToken())
  if (res.status !== 401) return res

  const newToken = await refreshAccessToken()
  if (!newToken) return forceLogin()

  const retry = await doFetch(newToken)
  if (retry.status === 401) return forceLogin()
  return retry
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const buildHeaders = (token: string | null): Record<string, string> => {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    }
    if (token) h["Authorization"] = `Bearer ${token}`
    const sellerId = getActiveSellerId()
    if (sellerId) h["X-Seller-ID"] = sellerId
    return h
  }

  const res = await withRefresh((token) =>
    fetch(`${API_URL}${path}`, { ...options, headers: buildHeaders(token) })
  )
  return parseResponse<T>(res)
}
