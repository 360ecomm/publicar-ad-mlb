import { apiFetch } from "./client"
import type {
  ListingDetail,
  ListingSummary,
  ListingsResponse,
  Condition,
  BulkResult,
  ListingAttributesRow,
  StatusCounts,
  RawPhotosOut,
} from "@/types/listing"

export interface CreateListingPayload {
  sku_description: string
  sku_brand: string
  price: number
  stock_quantity: number
  condition: Condition
  listing_type_id?: string
}

export async function createListing(
  payload: CreateListingPayload
): Promise<ListingSummary> {
  return apiFetch<ListingSummary>("/api/v1/listings", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export interface GetListingsParams {
  /**
   * Um ou vários status. Cada item vira um `status=` repetido na query
   * (`?status=a&status=b`), que é o formato que o backend espera. String
   * simples continua aceita para não quebrar quem passa um status só.
   * Vazio ou ausente não manda o parâmetro.
   */
  status?: string | string[]
  /** Busca por SKU, título, descrição, marca ou MLB. Vazio não vai na query. */
  search?: string
  page?: number
  page_size?: number
}

export async function getListings(
  params?: GetListingsParams
): Promise<ListingsResponse> {
  const query = new URLSearchParams()
  const statuses =
    typeof params?.status === "string" ? [params.status] : params?.status ?? []
  for (const status of statuses) {
    if (status) query.append("status", status)
  }
  if (params?.search) query.set("search", params.search)
  if (params?.page) query.set("page", String(params.page))
  if (params?.page_size) query.set("page_size", String(params.page_size))
  const qs = query.toString()
  return apiFetch<ListingsResponse>(`/api/v1/listings${qs ? `?${qs}` : ""}`)
}

/** Contagem por status do seller ativo, direto do banco (nunca do que está carregado). */
export async function getStatusCounts(): Promise<StatusCounts> {
  return apiFetch<StatusCounts>("/api/v1/listings/status-counts")
}

/**
 * URLs das fotos brutas do anúncio (botão "ver original" da revisão), sob
 * demanda. O servidor resolve o que existe no bucket público; quem carrega a
 * imagem é o navegador. Lista vazia não é erro.
 */
export async function getRawPhotos(id: string): Promise<RawPhotosOut> {
  return apiFetch<RawPhotosOut>(`/api/v1/listings/${id}/raw-photos`)
}

export async function getListing(id: string): Promise<ListingDetail> {
  return apiFetch<ListingDetail>(`/api/v1/listings/${id}`)
}

export async function deleteListing(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/listings/${id}`, { method: "DELETE" })
}

export async function startPipeline(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(`/api/v1/listings/${id}/pipeline/start`, {
    method: "POST",
  })
}

export async function retryPipeline(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(`/api/v1/listings/${id}/pipeline/retry`, {
    method: "POST",
  })
}

export async function selectTitle(
  listingId: string,
  titleId: string
): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(
    `/api/v1/listings/${listingId}/titles/${titleId}/select`,
    { method: "POST" }
  )
}

export interface AttributeInput {
  attribute_id: string
  value_id?: string
  value_name: string
}

export async function submitAttributes(
  listingId: string,
  attributes: AttributeInput[]
): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(`/api/v1/listings/${listingId}/attributes`, {
    method: "PUT",
    body: JSON.stringify({ attributes }),
  })
}

export async function generateImages(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(
    `/api/v1/listings/${id}/pipeline/generate_images`,
    { method: "POST" }
  )
}

export async function resumeRawPhotos(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(
    `/api/v1/listings/${id}/pipeline/resume_raw_photos`,
    { method: "POST" }
  )
}

export async function resumeAiEngine(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(
    `/api/v1/listings/${id}/pipeline/resume_ai_engine`,
    { method: "POST" }
  )
}

export async function approveImages(
  id: string,
  approved_ids: string[]
): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(`/api/v1/listings/${id}/images/approve`, {
    method: "POST",
    body: JSON.stringify({ approved_ids }),
  })
}

export async function publishListing(id: string): Promise<ListingSummary> {
  return apiFetch<ListingSummary>(`/api/v1/listings/${id}/pipeline/publish`, {
    method: "POST",
  })
}

export async function bulkStartPipeline(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/start-pipeline", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkApproveTitles(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/approve-titles", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkRejectTitles(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/reject-titles", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkApproveImages(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/approve-images", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkGenerateImages(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/generate-images", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkPublish(listingIds: string[]): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/publish", {
    method: "POST",
    body: JSON.stringify({ listing_ids: listingIds }),
  })
}

export async function bulkFillAttribute(payload: {
  listing_ids: string[]
  attribute_id: string
  value_name: string
  value_id?: string | null
}): Promise<BulkResult> {
  return apiFetch<BulkResult>("/api/v1/listings/bulk/attribute", {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export async function getListingsForGrid(): Promise<ListingAttributesRow[]> {
  return apiFetch<ListingAttributesRow[]>("/api/v1/listings/bulk/attributes")
}

export async function activateListing(id: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/listings/${id}/activate`, { method: "POST" })
}
