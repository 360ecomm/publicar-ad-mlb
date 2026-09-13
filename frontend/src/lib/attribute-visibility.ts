import type { AttributeOut } from "@/types/listing"

/**
 * O que o operador vê na tela de atributos.
 *
 * A regra de "editável" NÃO mora aqui: `is_editable` chega pronto do backend
 * (`ListingAttribute.is_editable`, falso só com `hidden` ou `read_only`), a
 * mesma disciplina de `is_candidate`. Este módulo só decide o que fazer com o
 * booleano: esconder os não editáveis, contar quantos foram escondidos e
 * avisar quando o anúncio inteiro é anterior à classificação (`tags` nulo em
 * todas as linhas, migração `e5f9c3b7a2d4`) — nesse caso nada é escondido.
 *
 * Exceção deliberada: obrigatório não editável APARECE. Escondê-lo tornaria a
 * validação de obrigatórios impossível de satisfazer. Não ocorre em MLB7863
 * nem em MLB6284 (verificado na API pública em 2026-09-13), mas as tags do ML
 * permitem a combinação.
 */
export interface AttributeVisibility {
  /** Na ordem original. */
  visible: AttributeOut[]
  /** Quantos não editáveis (e não obrigatórios) foram escondidos. */
  hiddenCount: number
  /** Nenhum atributo do anúncio tem `tags` gravadas: lista completa, com aviso. */
  unclassified: boolean
}

export function classifyAttributes(attributes: AttributeOut[]): AttributeVisibility {
  const unclassified = attributes.length > 0 && attributes.every((a) => a.tags == null)
  const visible = attributes.filter((a) => a.is_editable || a.is_required)
  return { visible, hiddenCount: attributes.length - visible.length, unclassified }
}

/** Valor cravado pela categoria: só com a tag `fixed` gravada. `tags` nulo nunca é fixo. */
export function isFixed(attr: AttributeOut): boolean {
  return attr.tags?.fixed === true
}

export interface FixedValue {
  value_id?: string
  value_name: string
}

/**
 * O valor que um atributo `fixed` mostra travado: o gravado, se houver; senão
 * o único `allowed_value` (o backend não pré-preenche `VEHICLE_TYPE`, então a
 * linha chega com `value_name` nulo). `null` quando não é fixo ou não há
 * valor único a mostrar.
 */
export function fixedValue(attr: AttributeOut): FixedValue | null {
  if (!isFixed(attr)) return null
  if (attr.value_name) {
    return { value_id: attr.value_id ?? undefined, value_name: attr.value_name }
  }
  const options = attr.allowed_values ?? []
  if (options.length === 1) {
    return { value_id: options[0].id, value_name: options[0].name }
  }
  return null
}
