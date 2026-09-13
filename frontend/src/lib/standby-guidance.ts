import type { ListingStatus } from "@/types/listing"

/**
 * Orientação dos dois standbys que abrem no DETALHE (não na revisão de
 * imagens: `pending_raw_photos` e `pending_ai_engine` não têm imagem para
 * revisar). Montada a partir do STATUS, nunca do `error_message` gravado: o
 * texto gravado é uma foto do momento em que o anúncio parou e não acompanha
 * correções (o FAROL01 em produção ainda tem a mensagem antiga só de `.jpg`).
 */
export interface StandbyGuidance {
  title: string
  body: string
  /** Rótulo do botão de retomada. "Verificar fotos agora" é o texto que a mensagem de erro do backend manda procurar. */
  action: string
}

export function standbyGuidance(
  status: ListingStatus | string,
  sku: string | null | undefined,
): StandbyGuidance | null {
  const doSku = sku && sku.trim() ? ` do SKU ${sku.trim()}` : ""
  if (status === "pending_raw_photos") {
    return {
      title: "Aguardando fotos brutas do produto",
      body:
        `As fotos${doSku} ainda não foram encontradas no bucket de fotos brutas do seller. ` +
        "O sistema verifica sozinho a cada 15 minutos; depois de subir os arquivos, você pode verificar agora.",
      action: "Verificar fotos agora",
    }
  }
  if (status === "pending_ai_engine") {
    return {
      title: "Aguardando o motor de imagem",
      body:
        "O motor de imagem respondeu como indisponível: crédito esgotado, chave inválida ou instabilidade. " +
        "O sistema tenta de novo sozinho a cada 15 minutos; depois de recarregar o crédito, você pode tentar agora.",
      action: "Tentar agora",
    }
  }
  return null
}

const TECHNICAL_MARKERS = ["sql", "traceback", "sqlalchemy", "exception"]

/**
 * Erro de `resume_raw_photos` / `resume_ai_engine` legível. O 409 de fotos
 * ausentes traz uma mensagem de negócio calculada NA HORA (quais arquivos
 * faltam, em quais formatos): essa passa inteira, porque não é o texto
 * gravado. O 409 por status errado vira "recarregue" sem nome interno de
 * status; erro técnico nunca vai cru.
 */
export function describeResumeError(status: number, detail: string | null | undefined): string {
  const text = (detail ?? "").trim()
  const lower = text.toLowerCase()
  if (status === 409 && lower.includes("indisponível no status")) {
    return "Este anúncio já saiu deste estado (outra ação ou o sistema avançou). Recarregue a tela."
  }
  if (!text || TECHNICAL_MARKERS.some((m) => lower.includes(m))) {
    return "Não foi possível retomar agora; tente de novo em instantes."
  }
  return text
}
