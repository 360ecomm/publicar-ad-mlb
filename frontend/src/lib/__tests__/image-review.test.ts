import { strict as assert } from "node:assert"
import { test } from "node:test"
import { canRegenerate, describeRegenerateError, regenerateWarning } from "../image-review"
import type { GalleryPosition, GallerySlot } from "../image-review"
import type { ImageOut } from "@/types/listing"

/**
 * `describeRegenerateError` casa o `detail` do backend POR TEXTO — não há
 * código de erro por caso. Esses testes existem porque o casamento já quebrou
 * uma vez: o backend passou de "apenas no status" para "apenas nos status"
 * (plural, ao aceitar dois) e o operador voltou a ver nome interno de status
 * na tela. As duas formas estão cobertas de propósito.
 */

const DETALHE_PLURAL =
  "Regeneração de imagem disponível apenas nos status 'pending_image_approval' e " +
  "'ready_to_publish' (atual: 'publishing')"

const DETALHE_SINGULAR =
  "Regeneração de imagem disponível apenas no status 'pending_image_approval' (atual: 'publishing')"

const FORA_DA_REVISAO =
  "Este anúncio saiu da revisão de imagens (outra ação ou o sistema avançou). Recarregue a tela."

test("409 de status fora da revisão: mensagem plural nova", () => {
  assert.equal(describeRegenerateError(409, DETALHE_PLURAL, 4), FORA_DA_REVISAO)
})

test("409 de status fora da revisão: mensagem singular antiga", () => {
  assert.equal(describeRegenerateError(409, DETALHE_SINGULAR, 4), FORA_DA_REVISAO)
})

test("409 de regeneração já em andamento", () => {
  const detalhe = "Regeneração em andamento na posição 2; aguarde."
  assert.equal(
    describeRegenerateError(409, detalhe, 2),
    "Benefícios já está sendo regenerada; aguarde ela terminar.",
  )
})

test("409 de posição já aprovada", () => {
  const detalhe = "Posição 0 já está aprovada; imagem aprovada não é regenerada"
  assert.equal(
    describeRegenerateError(409, detalhe, 0),
    "Capa já está aprovada e não é regenerada.",
  )
})

test("503 diz que o pedido não foi registrado", () => {
  const detalhe =
    "Fila de processamento indisponível no momento; o pedido não foi registrado. Tente de novo em instantes."
  assert.equal(
    describeRegenerateError(503, detalhe, 1),
    "Fila de processamento indisponível; o pedido não foi registrado. Tente de novo em instantes.",
  )
})

test("erro sem detalhe cai no fallback com o rótulo da posição", () => {
  assert.equal(
    describeRegenerateError(500, null, 3),
    "Não foi possível regenerar Detalhes agora; tente de novo.",
  )
})

test("erro técnico cru nunca chega à tela", () => {
  const detalhe = "Traceback (most recent call last): sqlalchemy.exc.IntegrityError"
  assert.equal(
    describeRegenerateError(500, detalhe, 4),
    "Não foi possível regenerar Ficha técnica agora; tente de novo.",
  )
})

// ---------------------------------------------------------------------------
// canRegenerate / regenerateWarning
// ---------------------------------------------------------------------------

const imagem = (over: Partial<ImageOut> = {}): ImageOut =>
  ({
    id: "img1",
    ml_picture_id: "ML1",
    status: "uploaded",
    approved: true,
    sort_order: 4,
    kind: "specs_ai",
    is_candidate: false,
    validation_error: null,
    ...over,
  }) as ImageOut

const slot = (over: Partial<GallerySlot> = {}): GallerySlot => ({
  position: 4 as GalleryPosition,
  label: "Ficha técnica",
  image: imagem(),
  state: "ready",
  reason: null,
  coverOrigin: null,
  ...over,
})

test("posição aprovada não regenera em pending_image_approval", () => {
  assert.equal(canRegenerate(slot(), "pending_image_approval"), false)
})

test("posição aprovada regenera em ready_to_publish", () => {
  assert.equal(canRegenerate(slot(), "ready_to_publish"), true)
})

test("posição reprovada no QA continua regenerável nos dois status", () => {
  const reprovada = slot({ image: imagem({ approved: false, ml_picture_id: null }), state: "qa_failed" })
  assert.equal(canRegenerate(reprovada, "pending_image_approval"), true)
  assert.equal(canRegenerate(reprovada, "ready_to_publish"), true)
})

test("posição em regeneração nunca regenera de novo", () => {
  const gerando = slot({ image: imagem({ approved: false, status: "generating" }), state: "generating" })
  assert.equal(canRegenerate(gerando, "pending_image_approval"), false)
  assert.equal(canRegenerate(gerando, "ready_to_publish"), false)
})

test("sem aviso fora de ready_to_publish em posição sem copy de LLM", () => {
  assert.equal(regenerateWarning(4, "pending_image_approval"), null)
})

test("posição 2 avisa sobre o texto do card", () => {
  const aviso = regenerateWarning(2, "pending_image_approval")
  assert.ok(aviso)
  assert.match(aviso.long, /texto do card pode mudar/)
  assert.equal(aviso.short, "O texto do card pode mudar.")
})

test("ready_to_publish avisa que perde a aprovação e volta para a revisão", () => {
  const aviso = regenerateWarning(4, "ready_to_publish")
  assert.ok(aviso)
  assert.match(aviso.long, /Ficha técnica/)
  assert.match(aviso.long, /desfaz a aprovação/)
  assert.match(aviso.long, /aprovar as imagens de novo/)
  assert.match(aviso.long, /descrição é gerada de novo/)
  assert.equal(aviso.short, "Desfaz a aprovação e volta para a revisão.")
})

test("ready_to_publish na posição 2 junta os dois avisos", () => {
  const aviso = regenerateWarning(2, "ready_to_publish")
  assert.ok(aviso)
  assert.match(aviso.long, /desfaz a aprovação/)
  assert.match(aviso.long, /texto do card pode mudar/)
  assert.equal(aviso.short, "Desfaz a aprovação e volta para a revisão. O texto do card pode mudar.")
})
