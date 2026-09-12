import {
  STATUS_GROUPS,
  STATUS_GROUP_OF,
  STATUS_LABELS,
  type ListingStatus,
  type StatusCounts,
  type StatusGroupKey,
} from "@/types/listing"

/** Chave sintética do item "Outros" (status legados, fora de ListingStatus). */
export const OUTROS_KEY = "__outros__"

export interface StatusSummaryItem {
  /** Status canônico, ou OUTROS_KEY para o agregado de legados. */
  status: string
  label: string
  count: number
  /** Os `status=` que filtram a fila por este item. */
  filterStatuses: string[]
  /** Só no item Outros: as chaves cruas que o backend devolveu. */
  legacyKeys?: string[]
}

export interface StatusSummaryGroup {
  key: StatusGroupKey
  label: string
  /** Soma das contagens dos status do bloco (inclui legados no bloco Concluído). */
  count: number
  items: StatusSummaryItem[]
  /** Os `status=` que filtram a fila por este bloco inteiro. */
  filterStatuses: string[]
}

export interface StatusSummary {
  groups: StatusSummaryGroup[]
  /** `total` como veio do backend. */
  total: number
  /** Soma dos três blocos; tem que ser igual a `total`. */
  sumOfGroups: number
  balanced: boolean
}

/**
 * Agrupa GET /listings/status-counts nos três blocos da barra.
 *
 * - Todo status de ListingStatus aparece no seu bloco, mesmo com zero: a
 *   barra não muda de tamanho a cada atualização.
 * - Chave de `by_status` fora de ListingStatus é status legado: vira um único
 *   item "Outros" dentro de Concluído, com as chaves cruas, e entra na soma.
 *   Nenhum anúncio pode sumir da conta.
 */
export function summarizeStatusCounts(counts: StatusCounts): StatusSummary {
  const byStatus = counts.by_status
  const legacyKeys = Object.keys(byStatus).filter(
    (key) => !(key in STATUS_GROUP_OF)
  )

  const groups: StatusSummaryGroup[] = STATUS_GROUPS.map((group) => {
    const items: StatusSummaryItem[] = group.statuses.map((status: ListingStatus) => ({
      status,
      label: STATUS_LABELS[status],
      count: byStatus[status] ?? 0,
      filterStatuses: [status],
    }))
    if (group.key === "done" && legacyKeys.length > 0) {
      items.push({
        status: OUTROS_KEY,
        label: "Outros",
        count: legacyKeys.reduce((sum, key) => sum + (byStatus[key] ?? 0), 0),
        filterStatuses: legacyKeys,
        legacyKeys,
      })
    }
    return {
      key: group.key,
      label: group.label,
      count: items.reduce((sum, item) => sum + item.count, 0),
      items,
      filterStatuses: items.flatMap((item) => item.filterStatuses),
    }
  })

  const sumOfGroups = groups.reduce((sum, group) => sum + group.count, 0)
  return { groups, total: counts.total, sumOfGroups, balanced: sumOfGroups === counts.total }
}
