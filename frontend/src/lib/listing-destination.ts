import type { ListingStatus } from "@/types/listing"

export interface ListingDestination {
  href: string
  /** Rótulo mostrado na fila, para o clique nunca surpreender. */
  label: string
}

/**
 * Para onde a linha da fila leva: direto à etapa que espera ação humana.
 * Qualquer outro status cai no detalhe. O SKU é sempre atalho para o
 * detalhe, independente disto (decisão de Daniel, tarefa 2 da fila).
 */
export function destinationFor(id: string, status: ListingStatus): ListingDestination {
  const base = `/listings/${id}`
  switch (status) {
    case "pending_title_approval":
      return { href: `${base}/titles`, label: "Escolher título" }
    case "pending_seller_attributes":
      return { href: `${base}/attributes`, label: "Preencher atributos" }
    case "pending_image_approval":
      return { href: `${base}/images`, label: "Revisar imagens" }
    case "ready_to_publish":
      return { href: `${base}/preview`, label: "Publicar" }
    default:
      return { href: base, label: "Ver detalhes" }
  }
}
