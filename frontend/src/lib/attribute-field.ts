import type { AttributeOut } from "@/types/listing"
import { isFixed } from "@/lib/attribute-visibility"

/**
 * Que tipo de campo um atributo do ML merece na tela.
 *
 * Quem decide é o `attribute_type`, NÃO a existência de `allowed_values`.
 * Os dois significam coisas diferentes, e o ML é explícito sobre isso
 * (developers.mercadolivre.com.br/pt_br/atributos, "Tipos de atributos
 * possíveis"):
 *
 *   - `list`  -> "só precisa o value_name de um dos valores possíveis":
 *                enumeração fechada.
 *   - `string`/`number` -> "você pode preencher com TEXTO LIVRE… sugerimos
 *                uma lista de valores conhecidos; mesmo assim, você também
 *                pode adicionar novos que não façam parte dessa lista".
 *
 * Tratar os dois como lista fechada não é um detalhe de UI: em MLB264201 o
 * `FLAVOR` é `string` com 12 sugestões, e o produto real é "Lichia", que não
 * está entre elas. Sendo obrigatório, salvar exigia escolher um sabor
 * ERRADO — e foi assim que "Chocolate" acabou gravado e impresso na ficha
 * técnica de um anúncio. A tela induziu o operador a gravar dado falso.
 *
 * O backend já concorda com esta regra: `listing_service._validar_valor`
 * recusa valor fora da lista SÓ quando `attribute_type == "list"`.
 */
export type AttributeFieldKind =
  /** Valor cravado pela categoria (tag `fixed`): mostrado travado. */
  | "fixed"
  /** Enumeração fechada: escolher uma das opções e nada mais. */
  | "closed-list"
  /** Sugestões oferecidas E texto livre aceito. */
  | "suggestions"
  /** Sem sugestão nenhuma: campo de texto puro. */
  | "free-text"

/**
 * Tipos em que `values` é enumeração fechada.
 *
 * `boolean` entra junto de `list` porque o ML exige o id do valor ("é
 * necessário enviar o id do valor; você pode consultá-lo na API de
 * atributos"), o que texto livre não produz.
 */
const TIPOS_FECHADOS = new Set(["list", "boolean"])

export function fieldKindFor(attr: AttributeOut): AttributeFieldKind {
  if (isFixed(attr)) return "fixed"

  const options = attr.allowed_values ?? []
  if (options.length === 0) return "free-text"

  // Tipo desconhecido cai no lado seguro (deixa digitar): o backend só
  // recusa valor fora da lista quando o tipo é `list`, então fechar um tipo
  // que ainda não conhecemos repetiria exatamente o defeito que esta regra
  // existe para apagar.
  return TIPOS_FECHADOS.has(attr.attribute_type) ? "closed-list" : "suggestions"
}

export interface Suggestion {
  id: string
  name: string
}

/**
 * A sugestão cujo nome é igual ao texto digitado (sem diferenciar
 * maiúsculas e com as pontas aparadas), ou `null`.
 *
 * Serve para o campo com sugestões gravar `value_id` + o nome EXATO do ML
 * quando o operador digita algo que existe na lista — e não gravar id
 * nenhum quando ele digita um valor novo, que é o que a documentação
 * prescreve ("para valores novos só deverá enviar o name").
 */
export function matchSuggestion(
  options: Suggestion[] | null | undefined,
  text: string,
): Suggestion | null {
  const alvo = text.trim().toLowerCase()
  if (!alvo) return null
  return (options ?? []).find((o) => o.name.trim().toLowerCase() === alvo) ?? null
}
