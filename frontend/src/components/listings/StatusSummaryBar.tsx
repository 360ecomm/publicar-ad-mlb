"use client"

import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { StatusSummary, StatusSummaryGroup } from "@/lib/status-summary"
import type { StatusGroupKey } from "@/types/listing"

export interface QueueFilter {
  group: StatusGroupKey | null
  /** Status individual dentro do bloco; null = o bloco inteiro. */
  status: string | null
}

interface Props {
  summary: StatusSummary | undefined
  isLoading: boolean
  filter: QueueFilter
  onSelectGroup: (group: StatusGroupKey) => void
  onSelectStatus: (group: StatusGroupKey, status: string) => void
  onClear: () => void
}

// Cor só para o que exige atenção: o bloco "Esperando você" ganha destaque
// quando tem algo; os outros ficam neutros.
const GROUP_ACCENT: Record<StatusGroupKey, string> = {
  processing: "border-t-slate-400",
  waiting: "border-t-amber-400",
  done: "border-t-green-400",
}

function GroupBlock({
  group,
  selected,
  selectedStatus,
  onSelect,
  onSelectStatus,
}: {
  group: StatusSummaryGroup
  selected: boolean
  selectedStatus: string | null
  onSelect: () => void
  onSelectStatus: (status: string) => void
}) {
  const attention = group.key === "waiting" && group.count > 0
  return (
    <div
      className={`rounded-lg border border-border border-t-4 ${GROUP_ACCENT[group.key]} bg-card ${
        selected ? "ring-2 ring-primary/40" : ""
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        title={selected ? "Clique de novo para limpar o filtro" : `Filtrar por ${group.label}`}
        className="w-full flex items-baseline justify-between px-4 py-3 text-left hover:bg-muted/50 rounded-t-md transition-colors"
      >
        <span className="text-sm font-medium text-foreground">{group.label}</span>
        <span
          className={`text-2xl font-bold tabular-nums ${
            attention ? "text-amber-600 dark:text-amber-400" : "text-foreground"
          }`}
        >
          {group.count}
        </span>
      </button>

      {selected && (
        <ul className="border-t border-border px-2 py-2 grid gap-0.5">
          {group.items.map((item) => {
            const active = selectedStatus === item.status
            const zero = item.count === 0
            return (
              <li key={item.status}>
                <button
                  type="button"
                  onClick={() => onSelectStatus(item.status)}
                  aria-pressed={active}
                  title={
                    item.legacyKeys
                      ? `Status fora da lista atual: ${item.legacyKeys.join(", ")}`
                      : active
                      ? "Clique de novo para voltar ao bloco inteiro"
                      : `Filtrar por ${item.label}`
                  }
                  className={`w-full flex items-center justify-between gap-2 rounded px-2 py-1 text-xs transition-colors ${
                    active ? "bg-primary/10 text-foreground font-medium" : "hover:bg-muted/60"
                  } ${zero ? "opacity-40" : ""}`}
                >
                  <span className="truncate">
                    {item.label}
                    {item.legacyKeys && (
                      <span className="ml-1 font-mono text-[10px] text-muted-foreground">
                        ({item.legacyKeys.join(", ")})
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums font-medium">{item.count}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function StatusSummaryBar({
  summary,
  isLoading,
  filter,
  onSelectGroup,
  onSelectStatus,
  onClear,
}: Props) {
  const hasFilter = filter.group !== null

  return (
    <div className="mb-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-start">
        {summary
          ? summary.groups.map((group) => (
              <GroupBlock
                key={group.key}
                group={group}
                selected={filter.group === group.key}
                selectedStatus={filter.group === group.key ? filter.status : null}
                onSelect={() => onSelectGroup(group.key)}
                onSelectStatus={(status) => onSelectStatus(group.key, status)}
              />
            ))
          : ["Processando", "Esperando você", "Concluído"].map((label) => (
              <div
                key={label}
                className="rounded-lg border border-border border-t-4 border-t-slate-200 bg-card px-4 py-3 flex items-baseline justify-between"
              >
                <span className="text-sm font-medium text-muted-foreground">{label}</span>
                <span className="text-2xl font-bold text-muted-foreground">
                  {isLoading ? "…" : "—"}
                </span>
              </div>
            ))}
      </div>

      <div className="flex items-center justify-between mt-2 min-h-[1.5rem]">
        <p className="text-xs text-muted-foreground">
          {summary ? (
            <>
              {summary.total} anúncio{summary.total !== 1 ? "s" : ""} no total
              {!summary.balanced && (
                <span className="ml-2 inline-flex items-center gap-1 text-amber-600">
                  <AlertTriangle className="w-3 h-3" />
                  a soma dos blocos ({summary.sumOfGroups}) não bate com o total
                </span>
              )}
            </>
          ) : null}
        </p>
        {hasFilter && (
          <Button variant="ghost" size="sm" onClick={onClear} className="h-7 text-xs">
            Todos
          </Button>
        )}
      </div>
    </div>
  )
}
