import type { AttributeOut, AttributesEditResponse, ListingStatus, ListingSummary } from "@/types/listing"

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

/**
 * Distingue `AttributesEditResponse` de `ListingSummary` pelo FORMATO real
 * da resposta, não pelo `mode` do formulário.
 *
 * `mode` vem de uma prop derivada do status do listing; se o componente pai
 * re-renderizar com um `mode` diferente entre o `mutate()` (que decide qual
 * requisição sai, `editAttributes` ou `submitAttributes`) e a resolução da
 * promise, o `onSuccess` que roda é o da renderização mais nova — com um
 * `mode` que não corresponde mais ao tipo do `result` já em trânsito. Sem
 * este guard, um cast (`result as AttributesEditResponse`) mentiria sobre o
 * tipo e `AttributeEditWarning` estouraria ao ler `stale_positions`/
 * `duplicated_fields` de um `ListingSummary`.
 */
export function isAttributesEditResponse(
  result: ListingSummary | AttributesEditResponse,
): result is AttributesEditResponse {
  return "stale_positions" in result
}
