import type { AttributeOut, ListingStatus } from "@/types/listing"

/**
 * Espelha `EDITABLE_ATTRIBUTE_STATUSES` em `backend/app/models/listing.py`.
 * A lista não tem CHECK no banco nem contrato compartilhado, então mudou lá,
 * muda aqui — o teste acima é o que cobra.
 */
export const EDITABLE_ATTRIBUTE_STATUSES: readonly ListingStatus[] = [
  "draft",
  "pending_title_approval",
  "pending_seller_attributes",
  "pending_description",
  "pending_raw_photos",
  "pending_ai_engine",
  "pending_image_approval",
  "ready_to_publish",
  "failed",
]

export function isEditableStatus(status: ListingStatus): boolean {
  return EDITABLE_ATTRIBUTE_STATUSES.includes(status)
}

export interface AttributeValue {
  value_id?: string
  value_name: string
}

export interface AttributeEditItem {
  attribute_id: string
  value_id?: string
  value_name: string
}

/**
 * Payload do PATCH de correção: só o que MUDOU, e **incluindo o que foi
 * esvaziado**.
 *
 * O envio do modo de preenchimento filtra todo valor vazio, e é o certo lá —
 * campo escondido simplesmente não entra e nada é apagado. Em correção esse
 * mesmo filtro tornaria impossível limpar um campo: o operador apagaria o
 * texto, o item sumiria do payload e o backend nunca saberia.
 */
export function buildEditPayload(
  attributes: AttributeOut[],
  values: Record<string, AttributeValue>,
): AttributeEditItem[] {
  const items: AttributeEditItem[] = []
  for (const attr of attributes) {
    const atual = values[attr.attribute_id]
    if (!atual) continue
    const antes = (attr.value_name ?? "").trim()
    const depois = (atual.value_name ?? "").trim()
    if (antes === depois) continue
    items.push({
      attribute_id: attr.attribute_id,
      value_id: atual.value_id,
      value_name: atual.value_name ?? "",
    })
  }
  return items
}
