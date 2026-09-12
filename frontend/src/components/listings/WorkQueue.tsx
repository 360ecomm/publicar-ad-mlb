"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { formatDistanceToNow } from "date-fns"
import { ptBR } from "date-fns/locale"
import { ChevronRight, ImageOff, Loader2, RefreshCw, Search, ListChecks } from "lucide-react"
import { getListings, getStatusCounts } from "@/lib/api/listings"
import { ApiError } from "@/lib/api/client"
import { useSeller } from "@/contexts/SellerContext"
import { useDebouncedValue } from "@/hooks/useDebouncedValue"
import { summarizeStatusCounts } from "@/lib/status-summary"
import { destinationFor } from "@/lib/listing-destination"
import { STATUS_GROUPS, STATUS_LABELS } from "@/types/listing"
import type { ListingStatus, ListingSummary, StatusGroupKey } from "@/types/listing"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent } from "@/components/ui/card"
import { ListingStatusBadge } from "./ListingStatusBadge"
import { StatusSummaryBar, type QueueFilter } from "./StatusSummaryBar"

const PAGE_SIZE = 50
const SEARCH_DELAY_MS = 300
/** Esquema de 5 posições: galeria completa tem 5 imagens oficiais aprovadas. */
const FULL_GALLERY = 5

/** `failed_step` guarda o status em que o anúncio parou; rótulo pelo STATUS_LABELS. */
function failedStepLabel(step: string | null | undefined): string {
  if (!step) return "—"
  return step in STATUS_LABELS ? STATUS_LABELS[step as ListingStatus] : step.replace(/_/g, " ")
}

function QueueRow({ listing, index }: { listing: ListingSummary; index: number }) {
  const router = useRouter()
  const destination = destinationFor(listing.id, listing.status)
  const detailHref = `/listings/${listing.id}`
  const isEven = index % 2 === 0
  const isFailed = listing.status === "failed"
  // Aviso, não falha: zero aprovadas é "ainda não revisado" e não recebe marca.
  const isIncomplete =
    listing.approved_image_count > 0 && listing.approved_image_count < FULL_GALLERY

  const go = () => router.push(destination.href)

  return (
    <tr
      className={`border-b border-border transition-colors cursor-pointer ${
        isEven ? "bg-background hover:bg-muted/50" : "bg-muted/20 hover:bg-muted/50"
      }`}
      onClick={go}
      onKeyDown={(e) => {
        if (e.key === "Enter") go()
      }}
      tabIndex={0}
      title={destination.label}
    >
      <td className="px-4 py-2.5 font-mono text-xs whitespace-nowrap">
        <Link
          href={detailHref}
          onClick={(e) => e.stopPropagation()}
          className="text-muted-foreground hover:text-foreground hover:underline underline-offset-2"
          title="Abrir detalhe do anúncio"
        >
          {listing.sku_external_id ?? "—"}
        </Link>
      </td>
      <td className="px-4 py-2.5 max-w-md">
        {listing.selected_title ? (
          <span className="block truncate font-medium text-foreground">{listing.selected_title}</span>
        ) : (
          <span
            className="block truncate text-muted-foreground italic"
            title="Descrição de origem: o título ainda não foi escolhido"
          >
            {listing.sku_description}
          </span>
        )}
        {isIncomplete && (
          <span
            className="inline-flex items-center gap-1 mt-0.5 text-[11px] text-amber-700 dark:text-amber-400"
            title={`${listing.approved_image_count} de ${FULL_GALLERY} imagens aprovadas`}
          >
            <ImageOff className="w-3 h-3" />
            Incompleto ({listing.approved_image_count}/{FULL_GALLERY})
          </span>
        )}
        {listing.mlb_id && (
          <span className="block font-mono text-[11px] text-blue-600 dark:text-blue-400">{listing.mlb_id}</span>
        )}
      </td>
      <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
        {listing.ml_category_id ?? "—"}
      </td>
      <td className="px-4 py-2.5 whitespace-nowrap">
        <ListingStatusBadge status={listing.status} />
      </td>
      <td
        className={`px-4 py-2.5 text-xs whitespace-nowrap ${
          isFailed ? "text-red-600 dark:text-red-400 font-medium" : "text-muted-foreground"
        }`}
      >
        {isFailed ? failedStepLabel(listing.failed_step) : "—"}
      </td>
      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
        {listing.created_via === "batch" ? "Lote" : "Manual"}
      </td>
      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
        {formatDistanceToNow(new Date(listing.updated_at), { addSuffix: true, locale: ptBR })}
      </td>
      <td className="px-4 py-2.5 text-xs whitespace-nowrap">
        <span
          className={`inline-flex items-center gap-1 ${
            destination.label === "Ver detalhes" ? "text-muted-foreground" : "text-foreground font-medium"
          }`}
        >
          {destination.label}
          <ChevronRight className="w-3.5 h-3.5" />
        </span>
      </td>
    </tr>
  )
}

function CenteredMessage({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col items-center justify-center h-64 gap-3">{children}</div>
}

const SETTINGS_LINK = (
  <Link href="/settings" className="text-sm font-medium text-primary underline underline-offset-4 hover:opacity-80">
    Ir para Configurações
  </Link>
)

export function WorkQueue() {
  const { activeSeller, isLoading: isSellerLoading } = useSeller()
  // Abre já filtrada em "Esperando você": decisão de produto (Daniel, 2026-09-12), não acaso.
  const [filter, setFilter] = useState<QueueFilter>({ group: "waiting", status: null })
  const [search, setSearch] = useState("")
  const [page, setPage] = useState(1)
  const debouncedSearch = useDebouncedValue(search.trim(), SEARCH_DELAY_MS)

  // Contagens sempre do banco, nunca do que está carregado na página.
  const countsQuery = useQuery({
    queryKey: ["listings", "status-counts", activeSeller?.id],
    queryFn: getStatusCounts,
    enabled: !!activeSeller,
    refetchOnWindowFocus: false,
  })
  const summary = useMemo(
    () => (countsQuery.data ? summarizeStatusCounts(countsQuery.data) : undefined),
    [countsQuery.data]
  )

  // Os `status=` da query derivam do filtro: item individual, bloco inteiro
  // (com os legados, no Concluído) ou nada. Sem contagem carregada, cai nos
  // status canônicos do bloco.
  const filterStatuses = useMemo<string[]>(() => {
    if (!filter.group) return []
    const group = summary?.groups.find((g) => g.key === filter.group)
    if (filter.status) {
      return group?.items.find((i) => i.status === filter.status)?.filterStatuses ?? [filter.status]
    }
    return group?.filterStatuses ?? STATUS_GROUPS.find((g) => g.key === filter.group)?.statuses ?? []
  }, [filter, summary])

  const listQuery = useQuery({
    queryKey: ["listings", "queue", activeSeller?.id, filterStatuses, debouncedSearch, page],
    queryFn: () =>
      getListings({
        status: filterStatuses,
        search: debouncedSearch || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    enabled: !!activeSeller,
    placeholderData: (prev) => prev,
    refetchOnWindowFocus: false,
  })

  const selectGroup = (group: StatusGroupKey) => {
    setPage(1)
    setFilter((prev) => (prev.group === group ? { group: null, status: null } : { group, status: null }))
  }
  const selectStatus = (group: StatusGroupKey, status: string) => {
    setPage(1)
    setFilter((prev) => (prev.status === status ? { group, status: null } : { group, status }))
  }
  const clearFilter = () => {
    setPage(1)
    setFilter({ group: null, status: null })
  }
  const refreshAll = () => {
    void countsQuery.refetch()
    void listQuery.refetch()
  }

  if (isSellerLoading) {
    return (
      <CenteredMessage>
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Carregando conta...</span>
      </CenteredMessage>
    )
  }

  if (!activeSeller) {
    return (
      <CenteredMessage>
        <p className="text-sm text-muted-foreground text-center">Nenhuma conta conectada.</p>
        {SETTINGS_LINK}
      </CenteredMessage>
    )
  }

  const error = listQuery.error ?? countsQuery.error
  if (error) {
    const isNoSeller = error instanceof ApiError && error.status === 422
    return (
      <CenteredMessage>
        <p className="text-sm text-muted-foreground text-center">
          {isNoSeller ? error.message : "Erro ao carregar anúncios. Tente atualizar."}
        </p>
        {isNoSeller ? (
          SETTINGS_LINK
        ) : (
          <Button variant="outline" size="sm" onClick={refreshAll}>
            Tentar de novo
          </Button>
        )}
      </CenteredMessage>
    )
  }

  const data = listQuery.data
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  const hasFilter = filterStatuses.length > 0 || debouncedSearch !== ""
  const isRefreshing = listQuery.isFetching || countsQuery.isFetching

  return (
    <div>
      <StatusSummaryBar
        summary={summary}
        isLoading={countsQuery.isLoading}
        filter={filter}
        onSelectGroup={selectGroup}
        onSelectStatus={selectStatus}
        onClear={clearFilter}
      />

      <div className="flex items-center gap-2 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Buscar por SKU, título, descrição, marca ou MLB..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            className="pl-9"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={refreshAll}
          disabled={isRefreshing}
          title="Recarregar a lista e as contagens"
        >
          <RefreshCw className={`w-4 h-4 mr-1.5 ${isRefreshing ? "animate-spin" : ""}`} />
          Atualizar
        </Button>
      </div>

      {listQuery.isLoading ? (
        <div className="flex items-center justify-center h-48">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.items.length === 0 ? (
        <div className="text-center py-20 text-muted-foreground">
          <ListChecks className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="font-medium">
            {hasFilter ? "Nenhum anúncio encontrado para este filtro ou busca." : "Nenhum anúncio."}
          </p>
          {hasFilter ? (
            <Button
              variant="outline"
              className="mt-4"
              onClick={() => {
                clearFilter()
                setSearch("")
              }}
            >
              Limpar filtros
            </Button>
          ) : (
            <div className="flex items-center justify-center gap-2 mt-4">
              <Button asChild variant="outline">
                <Link href="/import">Importar planilha</Link>
              </Button>
              <Button asChild variant="outline">
                <Link href="/listings/new">Novo anúncio</Link>
              </Button>
            </div>
          )}
        </div>
      ) : (
        <>
          <Card>
            <CardContent className="p-0">
              <div className={`overflow-x-auto ${listQuery.isPlaceholderData ? "opacity-60" : ""}`}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-xs uppercase tracking-wide">
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">SKU</th>
                      <th className="text-left px-4 py-3 font-medium">Título</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Categoria</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Status</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Etapa da falha</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Origem</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Atualizado</th>
                      <th className="text-left px-4 py-3 font-medium whitespace-nowrap">Ação</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((listing, i) => (
                      <QueueRow key={listing.id} listing={listing} index={i} />
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <div className="flex items-center justify-between mt-3">
            <p className="text-xs text-muted-foreground">
              {data.total} anúncio{data.total !== 1 ? "s" : ""}
              {hasFilter ? " neste filtro" : ""}
            </p>
            {totalPages > 1 && (
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  Anterior
                </Button>
                <span className="text-sm text-muted-foreground">
                  Página {page} de {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                >
                  Próxima
                </Button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
