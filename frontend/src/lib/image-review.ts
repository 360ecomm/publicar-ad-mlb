import type { ImageOut } from "@/types/listing"
import { sanitizeBulkError } from "./bulk-actions"

/**
 * Regras puras da tela de revisão de imagens por posição.
 *
 * O esquema de 5 posições é o único caminho de imagens (ver CLAUDE.md, "Ordem
 * das imagens do anúncio"). A tela mostra SEMPRE as 5, na mesma ordem, com o
 * rótulo vindo da POSIÇÃO (`sort_order`), nunca do `kind`: `cover_ai` e
 * `specs_ai` são também os kinds das candidatas das Frentes A/B, e a capa pode
 * ser `cover_deterministic` (recorte sem IA) quando a IA falhou.
 */
export const GALLERY_POSITIONS = [0, 1, 2, 3, 4] as const
export type GalleryPosition = (typeof GALLERY_POSITIONS)[number]

export const POSITION_LABELS: Record<GalleryPosition, string> = {
  0: "Capa",
  1: "Apresentação",
  2: "Benefícios",
  3: "Detalhes",
  4: "Ficha técnica",
}

/**
 * - `ready`: subiu ao ML (`ml_picture_id`), pode ser aprovada
 * - `qa_failed`: a IA produziu e o QA reprovou (`validation_failed`); motivo em `reason`
 * - `generation_failed`: a regeneração não produziu imagem; motivo em `reason`
 * - `generating`: placeholder de regeneração em andamento
 * - `missing`: nenhuma linha nesta posição
 */
export type SlotState = "ready" | "qa_failed" | "generation_failed" | "generating" | "missing"

export interface GallerySlot {
  position: GalleryPosition
  label: string
  image: ImageOut | null
  state: SlotState
  /** Motivo da falha, já saneado (`sanitizeBulkError`): nunca texto técnico cru. */
  reason: string | null
  /** Só na capa: se veio da IA ou do recorte determinístico. Muda o que o operador espera ver. */
  coverOrigin: "ia" | "recorte" | null
}

/** Quando há mais de uma linha na mesma posição, qual vence na tela (menor = vence). */
const STATE_PRIORITY: Record<SlotState, number> = {
  generating: 0,
  ready: 1,
  qa_failed: 2,
  generation_failed: 3,
  missing: 9,
}

function stateOf(image: ImageOut): Exclude<SlotState, "missing"> {
  if (image.status === "generating") return "generating"
  if (image.ml_picture_id) return "ready"
  if (image.status === "generation_failed") return "generation_failed"
  return "qa_failed"
}

function coverOriginOf(position: number, image: ImageOut | null): GallerySlot["coverOrigin"] {
  if (position !== 0 || image === null) return null
  if (image.kind === "cover_deterministic") return "recorte"
  if (image.kind === "cover_ai") return "ia"
  return null
}

function isGalleryPosition(n: number): n is GalleryPosition {
  return (GALLERY_POSITIONS as readonly number[]).includes(n)
}

/**
 * As 5 posições da galeria, sempre presentes. Candidatas (`is_candidate`,
 * calculado no backend) ficam de fora mesmo que o `sort_order` caia em 0..4:
 * na galeria elas apareceriam como mais uma imagem aprovável e criariam duas
 * capas. Com mais de uma linha na mesma posição (regeneração em andamento,
 * reprovada do QA ao lado da anterior), a de maior prioridade representa o slot.
 */
export function buildGallerySlots(images: ImageOut[]): GallerySlot[] {
  const escolhida = new Map<GalleryPosition, ImageOut>()
  for (const image of images) {
    if (image.is_candidate) continue
    if (!isGalleryPosition(image.sort_order)) continue
    const atual = escolhida.get(image.sort_order)
    if (atual === undefined || STATE_PRIORITY[stateOf(image)] <= STATE_PRIORITY[stateOf(atual)]) {
      escolhida.set(image.sort_order, image)
    }
  }
  return GALLERY_POSITIONS.map((position) => {
    const image = escolhida.get(position) ?? null
    const state: SlotState = image === null ? "missing" : stateOf(image)
    const falhou = state === "qa_failed" || state === "generation_failed"
    return {
      position,
      label: POSITION_LABELS[position],
      image,
      state,
      reason: falhou ? sanitizeBulkError(image?.validation_error).message : null,
      coverOrigin: coverOriginOf(position, image),
    }
  })
}

/** Existe candidata (Frentes A/B) de capa ou de ficha? A escolha é de outra tela; aqui só o aviso. */
export function candidateAlternatives(images: ImageOut[]): { cover: boolean; specs: boolean } {
  const candidatas = images.filter((i) => i.is_candidate)
  return {
    cover: candidatas.some((i) => i.kind === "cover_ai" || i.kind === "cover_deterministic"),
    specs: candidatas.some((i) => i.kind === "specs_ai" || i.kind === "card_specs"),
  }
}

/** Seleção inicial: tudo que está pronto. O caso normal (aprovar as 5) é um clique. */
export function defaultSelection(slots: GallerySlot[]): string[] {
  return slots.filter((s) => s.state === "ready" && s.image !== null).map((s) => s.image!.id)
}

/**
 * Ids a enviar em `approved_ids`, SEMPRE em ordem crescente de posição.
 *
 * A ordem importa: `approve_images` renumera o `sort_order` na ordem em que
 * os ids chegam, e é essa a ordem publicada no ML (CLAUDE.md, "A aprovação
 * individual renumera; a em massa não"). A galeria antiga montava a lista a
 * partir de um `Set` — ou seja, na ordem dos cliques: desmarcar e remarcar a
 * capa por último a jogava para o fim e deixava a posição 0 vaga. Só entram
 * posições `ready`: um id de posição reprovada, mesmo presente no set, não
 * pode virar imagem aprovada sem `ml_picture_id`.
 */
export function orderedApprovedIds(slots: GallerySlot[], selected: ReadonlySet<string>): string[] {
  return [...slots]
    .sort((a, b) => a.position - b.position)
    .filter((s) => s.state === "ready" && s.image !== null && selected.has(s.image.id))
    .map((s) => s.image!.id)
}

export interface ApprovalPlan {
  /** Posições prontas (aprováveis) nesta galeria. */
  available: number
  /** Quantas imagens o anúncio terá se aprovar agora. */
  total: number
  /** Rótulos das posições prontas que o operador desmarcou, em ordem de posição. */
  excludedLabels: string[]
  /** Aprovar com menos que o disponível pede confirmação; todas marcadas, não. */
  needsConfirmation: boolean
  /** Alguma posição em regeneração: o backend recusaria com 409, então nem oferecer. */
  blockedByGenerating: boolean
  canApprove: boolean
}

export function approvalPlan(slots: GallerySlot[], selected: ReadonlySet<string>): ApprovalPlan {
  const prontas = slots.filter((s) => s.state === "ready" && s.image !== null)
  const ids = orderedApprovedIds(slots, selected)
  const excludedLabels = prontas.filter((s) => !selected.has(s.image!.id)).map((s) => s.label)
  const blockedByGenerating = slots.some((s) => s.state === "generating")
  return {
    available: prontas.length,
    total: ids.length,
    excludedLabels,
    needsConfirmation: ids.length > 0 && excludedLabels.length > 0,
    blockedByGenerating,
    canApprove: ids.length > 0 && !blockedByGenerating,
  }
}

/** URL pública da imagem no CDN do ML a partir do `ml_picture_id`. */
export function mlPictureUrl(mlPictureId: string): string {
  return `https://http2.mlstatic.com/D_NQ_NP_${mlPictureId}-V.jpg`
}
