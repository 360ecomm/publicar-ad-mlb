import { strict as assert } from "node:assert"
import { test } from "node:test"

import { fieldKindFor, matchSuggestion } from "./attribute-field"
import type { AttributeOut } from "@/types/listing"

function attr(over: Partial<AttributeOut> = {}): AttributeOut {
  return {
    id: "1",
    attribute_id: "X",
    attribute_name: "X",
    attribute_type: "string",
    is_required: false,
    value_id: null,
    value_name: null,
    source: "ai",
    allowed_values: null,
    tags: null,
    is_editable: true,
    ...over,
  }
}

const SUGESTOES = [
  { id: "2517712", name: "Chocolate" },
  { id: "2472069", name: "Morango" },
]

test("list com sugestões: lista fechada", () => {
  assert.equal(
    fieldKindFor(attr({ attribute_type: "list", allowed_values: SUGESTOES })),
    "closed-list",
  )
})

test("string com sugestões: sugestões MAIS texto livre (o caso do FLAVOR)", () => {
  assert.equal(
    fieldKindFor(attr({ attribute_type: "string", allowed_values: SUGESTOES })),
    "suggestions",
  )
})

test("string sem sugestões: texto livre", () => {
  assert.equal(fieldKindFor(attr({ attribute_type: "string", allowed_values: null })), "free-text")
  assert.equal(fieldKindFor(attr({ attribute_type: "string", allowed_values: [] })), "free-text")
})

test("boolean com sugestões: lista fechada (o ML exige o id)", () => {
  assert.equal(
    fieldKindFor(attr({ attribute_type: "boolean", allowed_values: SUGESTOES })),
    "closed-list",
  )
})

test("number e number_unit com sugestões: texto livre com sugestões", () => {
  assert.equal(
    fieldKindFor(attr({ attribute_type: "number", allowed_values: SUGESTOES })),
    "suggestions",
  )
  assert.equal(
    fieldKindFor(attr({ attribute_type: "number_unit", allowed_values: SUGESTOES })),
    "suggestions",
  )
})

test("tipo desconhecido com sugestões: permite digitar", () => {
  // Lado seguro: o backend só recusa valor fora da lista quando o tipo é
  // `list` (listing_service._validar_valor). Fechar um tipo que não
  // conhecemos repetiria o defeito que esta correção existe para apagar.
  assert.equal(
    fieldKindFor(attr({ attribute_type: "tipo_novo_do_ml", allowed_values: SUGESTOES })),
    "suggestions",
  )
})

test("fixed continua travado, qualquer que seja o tipo", () => {
  assert.equal(
    fieldKindFor(attr({ attribute_type: "list", allowed_values: SUGESTOES, tags: { fixed: true } })),
    "fixed",
  )
  assert.equal(
    fieldKindFor(attr({ attribute_type: "string", allowed_values: SUGESTOES, tags: { fixed: true } })),
    "fixed",
  )
  assert.equal(
    fieldKindFor(attr({ attribute_type: "string", allowed_values: null, tags: { fixed: true } })),
    "fixed",
  )
})

test("matchSuggestion: texto igual a uma sugestão devolve id e o nome exato do ML", () => {
  assert.deepEqual(matchSuggestion(SUGESTOES, "chocolate"), { id: "2517712", name: "Chocolate" })
  assert.deepEqual(matchSuggestion(SUGESTOES, "  Morango "), { id: "2472069", name: "Morango" })
})

test("matchSuggestion: texto fora da lista não inventa id", () => {
  assert.equal(matchSuggestion(SUGESTOES, "Lichia"), null)
  assert.equal(matchSuggestion(SUGESTOES, ""), null)
  assert.equal(matchSuggestion(null, "Lichia"), null)
})
