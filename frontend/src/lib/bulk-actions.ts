import type { BulkResult, ListingStatus } from "@/types/listing"

/**
 * Regras puras da ação em massa na fila: qual ação cada status admite,
 * o que fazer com seleção mista, e como transformar o resultado do
 * backend em algo legível sem despejar erro técnico na tela.
 */

export type BulkActionId =
  | "start_pipeline"
  | "approve_titles"
  | "reject_titles"
  | "generate_images"
  | "approve_images"
  | "publish"
  | "fill_attributes"

export interface BulkActionSpec {
  id: BulkActionId
  label: string
  tone: "primary" | "danger" | "neutral"
  /** Só publicar: irreversível, manda anúncio ao ar no ML. */
  confirm: boolean
  /** Ação de navegação (sem chamada em massa). */
  href?: string
}

// Tabela conferida contra o backend: cada endpoint bulk recusa item fora do
// status esperado com "estado inválido". Status ausente aqui = nenhuma ação.
const ACTIONS_BY_STATUS: Partial<Record<ListingStatus, BulkActionSpec[]>> = {
  draft: [{ id: "start_pipeline", label: "Iniciar pipeline", tone: "primary", confirm: false }],
  pending_title_approval: [
    { id: "approve_titles", label: "Aprovar títulos", tone: "primary", confirm: false },
    { id: "reject_titles", label: "Reprovar títulos", tone: "danger", confirm: false },
  ],
  pending_seller_attributes: [
    { id: "fill_attributes", label: "Preencher atributos", tone: "neutral", confirm: false, href: "/listings/attributes" },
  ],
  pending_description: [{ id: "generate_images", label: "Gerar imagens", tone: "primary", confirm: false }],
  pending_image_approval: [{ id: "approve_images", label: "Aprovar imagens", tone: "primary", confirm: false }],
  ready_to_publish: [{ id: "publish", label: "Publicar", tone: "primary", confirm: true }],
}

export type BulkAvailability =
  | { kind: "empty" }
  | { kind: "mixed"; statuses: ListingStatus[] }
  | { kind: "none"; status: ListingStatus }
  | { kind: "actions"; status: ListingStatus; actions: BulkActionSpec[] }

/** Decide as ações a partir dos status dos selecionados (não de coluna de quadro). */
export function bulkAvailabilityFor(statuses: Iterable<ListingStatus>): BulkAvailability {
  const distinct = Array.from(new Set(statuses))
  if (distinct.length === 0) return { kind: "empty" }
  if (distinct.length > 1) return { kind: "mixed", statuses: distinct }
  const status = distinct[0]
  const actions = ACTIONS_BY_STATUS[status]
  return actions ? { kind: "actions", status, actions } : { kind: "none", status }
}

const TECHNICAL_MARKERS = ["sql", "traceback", "sqlalchemy"]
const TECHNICAL_MAX_LENGTH = 200
const GENERIC_MESSAGE = "erro interno ao processar este anúncio; detalhe registrado no console"

/**
 * Erro de negócio ("estado inválido", "nenhuma imagem aprovável") passa como
 * veio. Erro técnico (SQL, Traceback, sqlalchemy ou texto longo) vira frase
 * genérica; o original fica para o console, nunca para a tela.
 */
export function sanitizeBulkError(raw: string | null | undefined): { message: string; technical: boolean } {
  const text = (raw ?? "").trim()
  if (!text) return { message: "falha sem motivo informado", technical: false }
  const lower = text.toLowerCase()
  const technical = text.length > TECHNICAL_MAX_LENGTH || TECHNICAL_MARKERS.some((m) => lower.includes(m))
  return technical ? { message: GENERIC_MESSAGE, technical: true } : { message: text, technical: false }
}

export interface BulkFailure {
  listing_id: string
  /** SKU quando conhecido; senão o id, para a linha nunca ficar sem identificação. */
  sku: string
  message: string
  technical: boolean
  raw: string | null
}

export interface BulkSummary {
  ok: number
  failed: number
  failures: BulkFailure[]
}

export function summarizeBulkResult(
  result: BulkResult,
  skuOf: (listingId: string) => string | null | undefined
): BulkSummary {
  const failures: BulkFailure[] = []
  let ok = 0
  for (const item of result.results) {
    if (item.success) {
      ok += 1
      continue
    }
    const { message, technical } = sanitizeBulkError(item.error)
    failures.push({
      listing_id: item.listing_id,
      sku: skuOf(item.listing_id) || item.listing_id,
      message,
      technical,
      raw: item.error ?? null,
    })
  }
  return { ok, failed: failures.length, failures }
}
