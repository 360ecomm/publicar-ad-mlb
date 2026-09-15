import { strict as assert } from "node:assert"
import { test } from "node:test"
import { buildEditPayload, isEditableStatus, isAttributesEditResponse } from "../attribute-edit"
import type { AttributeOut, AttributesEditResponse, ListingSummary } from "@/types/listing"

const attr = (id: string, value_name: string | null): AttributeOut =>
  ({
    attribute_id: id,
    attribute_name: id,
    value_id: null,
    value_name,
    attribute_type: "string",
    is_required: false,
    allowed_values: null,
    tags: null,
    is_editable: true,
  }) as AttributeOut

test("payload de edição inclui campo esvaziado", () => {
  const atual = { FLAVOR: { value_name: "" } }
  const payload = buildEditPayload([attr("FLAVOR", "Chocolate")], atual)
  assert.deepEqual(payload, [{ attribute_id: "FLAVOR", value_id: undefined, value_name: "" }])
})

test("payload de edição omite o que não mudou", () => {
  const atual = { FLAVOR: { value_name: "Chocolate" } }
  assert.deepEqual(buildEditPayload([attr("FLAVOR", "Chocolate")], atual), [])
})

test("payload de edição inclui valor trocado", () => {
  const atual = { FLAVOR: { value_name: "Lichia" } }
  const payload = buildEditPayload([attr("FLAVOR", "Chocolate")], atual)
  assert.equal(payload.length, 1)
  assert.equal(payload[0].value_name, "Lichia")
})

test("valor nulo no servidor conta como vazio", () => {
  const atual = { FLAVOR: { value_name: "" } }
  assert.deepEqual(buildEditPayload([attr("FLAVOR", null)], atual), [])
})

test("atributo fixed sem valor gravado não entra no payload de correção", () => {
  // O formulário pré-preenche `fixed` a partir do único `allowed_value` (ver
  // `fixedValue`), então o estado da tela tem VEHICLE_TYPE preenchido mesmo
  // com o banco vazio. O operador só mexeu no FLAVOR.
  const fixo = {
    ...attr("VEHICLE_TYPE", null),
    tags: { fixed: true },
    allowed_values: [{ id: "1", name: "Carro" }],
  } as AttributeOut
  const atual = {
    VEHICLE_TYPE: { value_id: "1", value_name: "Carro" },
    FLAVOR: { value_name: "Lichia" },
  }
  const payload = buildEditPayload([fixo, attr("FLAVOR", "Chocolate")], atual)
  assert.deepEqual(payload, [{ attribute_id: "FLAVOR", value_id: undefined, value_name: "Lichia" }])
})

test("status editáveis batem com o backend", () => {
  for (const s of [
    "draft", "pending_title_approval", "pending_seller_attributes",
    "pending_description", "pending_raw_photos", "pending_ai_engine",
    "pending_image_approval", "ready_to_publish", "failed",
  ]) assert.equal(isEditableStatus(s as never), true, s)

  for (const s of [
    "generating_title", "predicting_category", "generating_images",
    "generating_description", "publishing", "published", "published_paused",
    "published_under_review",
  ]) assert.equal(isEditableStatus(s as never), false, s)
})

const listingSummary = {
  id: "l1",
  sku_external_id: "SKU1",
  sku_brand: "Marca",
  selected_title: null,
  sku_description: "desc",
  ml_category_id: null,
  approved_image_count: 0,
  status: "ready_to_publish",
  created_via: "manual",
  mlb_id: null,
  created_at: "2026-09-15T00:00:00Z",
  updated_at: "2026-09-15T00:00:00Z",
} as ListingSummary

const attributesEditResponse = {
  listing: listingSummary,
  stale_positions: [4],
  duplicated_fields: [],
} as AttributesEditResponse

test("isAttributesEditResponse aceita a resposta do PATCH", () => {
  assert.equal(isAttributesEditResponse(attributesEditResponse), true)
})

test("isAttributesEditResponse recusa um ListingSummary (resposta do PUT)", () => {
  assert.equal(isAttributesEditResponse(listingSummary), false)
})
