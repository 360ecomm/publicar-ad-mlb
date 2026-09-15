export type Condition = "new" | "used"

export type ListingStatus =
  | "draft"
  | "generating_title"
  | "pending_title_approval"
  | "predicting_category"
  | "pending_seller_attributes"
  | "pending_description"
  | "generating_images"
  | "pending_raw_photos"
  | "pending_ai_engine"
  | "pending_image_approval"
  | "generating_description"
  | "ready_to_publish"
  | "publishing"
  | "published"
  | "published_under_review"
  | "published_paused"
  | "failed"

export interface ListingSummary {
  id: string
  sku_external_id: string | null
  sku_brand: string
  selected_title: string | null
  /** Descrição de origem: o único texto que existe antes de o título ser escolhido. */
  sku_description: string
  ml_category_id: string | null
  /**
   * Imagens oficiais aprovadas (sort_order < 90), calculado no backend.
   * 0 = ainda não revisado; 1–4 = anúncio incompleto; 5 = galeria completa.
   */
  approved_image_count: number
  status: ListingStatus
  created_via: "manual" | "batch"
  mlb_id: string | null
  created_at: string
  updated_at: string
  failed_step?: string | null
}

export interface TitleOption {
  id: string
  title_text: string
  ai_score: number | null
  selected: boolean
}

export interface AttributeOut {
  id: string
  attribute_id: string
  attribute_name: string
  attribute_type: string
  is_required: boolean
  value_id: string | null
  value_name: string | null
  source: string
  allowed_values: { id: string; name: string }[] | null
  /** Tags do ML como vieram (hidden, read_only, fixed, required...). */
  tags: Record<string, boolean> | null
  /** Calculado no backend (ListingAttribute.is_editable): nao reimplementar. */
  is_editable: boolean
}

export interface ImageOut {
  id: string
  ml_picture_id: string | null
  status: string
  approved: boolean
  sort_order: number
  kind: string
  is_candidate: boolean
  validation_error: string | null
}

export interface JobOut {
  id: string
  job_type: string
  status: string
  error_message: string | null
  attempts: number
  created_at: string
}

export interface ListingDetail extends ListingSummary {
  sku_description: string
  price: number
  stock_quantity: number
  condition: Condition
  listing_type_id: string
  ml_category_id: string | null
  error_message: string | null
  description_html: string | null
  titles: TitleOption[]
  attributes: AttributeOut[]
  images: ImageOut[]
  jobs: JobOut[]
}

export interface ListingsResponse {
  items: ListingSummary[]
  total: number
  page: number
  page_size: number
}

/**
 * Resposta de GET /listings/status-counts. `by_status` é indexado por string,
 * NÃO por ListingStatus: o backend devolve também status legados fora da
 * lista, de propósito, para que nenhum anúncio suma da conta. Um
 * Record<ListingStatus, number> faria o legado sumir do total exibido.
 */
export interface StatusCounts {
  by_status: { [status: string]: number }
  total: number
}

/** Fotos brutas de UM SKU do anúncio, em ordem ({sku}-1, -2...). */
export interface RawPhotoGroup {
  sku: string
  urls: string[]
}

/**
 * GET /listings/{id}/raw-photos. Agrupado por SKU (um anúncio pode virar
 * kit). `configured=false` = seller sem bucket de fotos brutas (groups vazio);
 * SKU sem foto vem como grupo com `urls` vazio. Nenhum dos dois é erro.
 */
export interface RawPhotosOut {
  configured: boolean
  groups: RawPhotoGroup[]
}

export const STATUS_LABELS: Record<ListingStatus, string> = {
  draft: "Rascunho",
  generating_title: "Gerando título",
  pending_title_approval: "Aguardando aprovação de título",
  predicting_category: "Predizendo categoria",
  pending_seller_attributes: "Aguardando atributos",
  pending_description: "Aguardando descrição",
  generating_images: "Gerando imagens",
  pending_raw_photos: "Aguardando fotos brutas",
  pending_ai_engine: "Aguardando motor de IA (crédito)",
  pending_image_approval: "Aguardando aprovação de imagens",
  generating_description: "Gerando descrição",
  ready_to_publish: "Pronto para publicar",
  publishing: "Publicando",
  published: "Publicado",
  published_under_review: "Em análise no ML",
  published_paused: "Pausado no ML",
  failed: "Com erro",
}

// ---------------------------------------------------------------------------
// Os três blocos da barra de resumo (fila de trabalho, bloco B)
//
// - processing: o sistema está trabalhando, nada a fazer.
// - waiting:    exige ação humana. `failed` fica aqui (erro exige decisão),
//               `draft` também (espera alguém iniciar o pipeline),
//               `ready_to_publish` idem (última decisão humana antes do ML)
//               e `published_paused` (anomalia: o ML pausou ou nós nunca
//               ativamos — o operador reativa pela tela do anúncio).
// - done:       no ar, ou em análise do ML (`published_under_review` — nada
//               a fazer daqui; a moderação é lá).
//
// STATUS_GROUP_OF é a única fonte: um Record<ListingStatus, ...> obriga cada
// um dos 17 status a aparecer EXATAMENTE uma vez (chave faltando ou repetida
// é erro de compilação). Status novo no backend, adicionado em ListingStatus,
// derruba o `tsc` até ganhar um bloco — nunca fica invisível na barra.
// ---------------------------------------------------------------------------

export type StatusGroupKey = "processing" | "waiting" | "done"

export const STATUS_GROUP_OF: Record<ListingStatus, StatusGroupKey> = {
  draft: "waiting",
  generating_title: "processing",
  pending_title_approval: "waiting",
  predicting_category: "processing",
  pending_seller_attributes: "waiting",
  pending_description: "waiting",
  generating_images: "processing",
  pending_raw_photos: "waiting",
  pending_ai_engine: "waiting",
  pending_image_approval: "waiting",
  generating_description: "processing",
  ready_to_publish: "waiting",
  publishing: "processing",
  published: "done",
  published_under_review: "done",
  published_paused: "waiting",
  failed: "waiting",
}

export interface StatusGroup {
  key: StatusGroupKey
  label: string
  statuses: ListingStatus[]
}

function statusesOf(group: StatusGroupKey): ListingStatus[] {
  return (Object.keys(STATUS_GROUP_OF) as ListingStatus[]).filter(
    (status) => STATUS_GROUP_OF[status] === group
  )
}

export const STATUS_GROUPS: StatusGroup[] = [
  { key: "processing", label: "Processando", statuses: statusesOf("processing") },
  { key: "waiting", label: "Esperando você", statuses: statusesOf("waiting") },
  { key: "done", label: "Concluído", statuses: statusesOf("done") },
]

/** @deprecated Usar STATUS_GROUPS / STATUS_GROUP_OF. Mantido porque listings/[id]/page.tsx ainda lê. */
export const PROCESSING_STATUSES: ListingStatus[] = statusesOf("processing")

export interface BulkItemResult {
  listing_id: string
  success: boolean
  error?: string | null
}

export interface BulkResult {
  processed: number
  failed: number
  results: BulkItemResult[]
}

export interface AttributeItem {
  attribute_id: string
  attribute_name: string
  value_name: string | null
  value_id: string | null
  is_required: boolean
}

export interface ListingAttributesRow {
  listing_id: string
  sku_external_id: string
  selected_title: string | null
  ml_category_id: string | null
  status: string
  attributes: AttributeItem[]
}

/** Espelho de `AttributesEditResponse` em `backend/app/schemas/listing.py`. */
export interface AttributesEditResponse {
  listing: ListingSummary
  /** Posições do esquema de 5 cujo texto impresso na imagem ficou velho. */
  stale_positions: number[]
  /** Atributos editados que existem em duplicata (`BRAND`, `MODEL`). */
  duplicated_fields: string[]
}
