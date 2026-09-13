import { getActiveSellerId, ApiError, withRefresh } from "./client"

// `??`, nao `||`: definida como VAZIA (producao, mesmo dominio) produz chamada
// relativa `/api/v1/...`; AUSENTE (desenvolvimento) cai no padrao local. Com
// `||`, vazia e ausente seriam a mesma coisa e producao chamaria localhost.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"

export interface BatchImportOut {
  id: string
  seller_id: string
  filename: string
  total_rows: number
  processed_rows: number
  failed_rows: number
  status: string
  created_at: string
  updated_at: string
}

export interface BatchImportRowOut {
  id: string
  row_number: number
  sku: string
  listing_id: string | null
  status: string
  error_message: string | null
}

export interface BatchImportDetail extends BatchImportOut {
  rows: BatchImportRowOut[]
}

async function handleResponse<T>(res: Response): Promise<T> {
  // 401 já tratado por withRefresh (redirect para /login); aqui chegam apenas outros erros
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new ApiError(res.status, data?.detail || "Erro desconhecido")
  return data as T
}

function authHeaders(token: string | null): Record<string, string> {
  const h: Record<string, string> = {}
  if (token) h["Authorization"] = `Bearer ${token}`
  const sellerId = getActiveSellerId()
  if (sellerId) h["X-Seller-ID"] = sellerId
  return h
}

export async function uploadBatch(file: File): Promise<BatchImportOut> {
  const form = new FormData()
  form.append("file", file)
  const res = await withRefresh((token) =>
    fetch(`${API_URL}/api/v1/import`, {
      method: "POST",
      headers: authHeaders(token),
      body: form,
    })
  )
  return handleResponse<BatchImportOut>(res)
}

export async function listBatches(): Promise<BatchImportOut[]> {
  const res = await withRefresh((token) =>
    fetch(`${API_URL}/api/v1/import`, { headers: authHeaders(token) })
  )
  return handleResponse<BatchImportOut[]>(res)
}

export async function getBatch(id: string): Promise<BatchImportDetail> {
  const res = await withRefresh((token) =>
    fetch(`${API_URL}/api/v1/import/${id}`, { headers: authHeaders(token) })
  )
  return handleResponse<BatchImportDetail>(res)
}
