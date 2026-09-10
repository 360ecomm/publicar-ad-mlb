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
  | "published_paused"
  | "failed"

export interface ListingSummary {
  id: string
  sku_external_id: string | null
  sku_brand: string
  selected_title: string | null
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
}

export interface ImageOut {
  id: string
  url_r2: string | null
  ml_picture_id: string | null
  status: string
  approved: boolean
  sort_order: number
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
  published_paused: "Pausado",
  failed: "Com erro",
}

export const PROCESSING_STATUSES: ListingStatus[] = [
  "generating_title",
  "predicting_category",
  "generating_images",
  "generating_description",
  "publishing",
]

export const WAITING_STATUSES: ListingStatus[] = [
  "pending_title_approval",
  "pending_seller_attributes",
  "pending_image_approval",
  "pending_raw_photos",
  "pending_ai_engine",
  "ready_to_publish",
]

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
