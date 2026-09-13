import { apiFetch, withRefresh, getActiveSellerId, ApiError } from "./client"
import type { Product, ProductPage, ProductUploadResult, ProductFormData } from "@/types/product"

// `??`, nao `||`: definida como VAZIA (producao, mesmo dominio) produz chamada
// relativa `/api/v1/...`; AUSENTE (desenvolvimento) cai no padrao local. Com
// `||`, vazia e ausente seriam a mesma coisa e producao chamaria localhost.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001"

function authHeaders(token: string | null): Record<string, string> {
  const h: Record<string, string> = {}
  if (token) h["Authorization"] = `Bearer ${token}`
  const sellerId = getActiveSellerId()
  if (sellerId) h["X-Seller-ID"] = sellerId
  return h
}

async function handleResponse<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new ApiError(res.status, data?.detail || "Erro desconhecido")
  return data as T
}

export async function getProducts(params?: {
  search?: string
  page?: number
  page_size?: number
}): Promise<ProductPage> {
  const qs = new URLSearchParams()
  if (params?.search) qs.set("search", params.search)
  if (params?.page) qs.set("page", String(params.page))
  if (params?.page_size) qs.set("page_size", String(params.page_size))
  const query = qs.toString() ? `?${qs}` : ""
  return apiFetch(`/api/v1/products${query}`)
}

export async function getProduct(sku: string): Promise<Product> {
  return apiFetch(`/api/v1/products/${encodeURIComponent(sku)}`)
}

export async function createProduct(data: ProductFormData): Promise<Product> {
  return apiFetch("/api/v1/products", {
    method: "POST",
    body: JSON.stringify(data),
  })
}

export async function updateProduct(sku: string, data: Partial<ProductFormData>): Promise<Product> {
  return apiFetch(`/api/v1/products/${encodeURIComponent(sku)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  })
}

export async function uploadProducts(file: File): Promise<ProductUploadResult> {
  const form = new FormData()
  form.append("file", file)
  const res = await withRefresh((token) =>
    fetch(`${API_URL}/api/v1/products/upload`, {
      method: "POST",
      headers: authHeaders(token),
      body: form,
    })
  )
  return handleResponse<ProductUploadResult>(res)
}
