"use client"

import { useState, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { getListings } from "@/lib/api/listings"
import { ApiError } from "@/lib/api/client"
import { useSeller } from "@/contexts/SellerContext"
import { ListingCard } from "./ListingCard"
import { BulkActionsBar } from "./BulkActionsBar"
import type { ListingStatus, ListingSummary } from "@/types/listing"
import { Loader2 } from "lucide-react"
import Link from "next/link"

type ColumnId = "fila" | "titulos" | "categoria" | "imagens" | "descricao" | "publicados"

const COLUMNS: {
  id: ColumnId
  title: string
  statuses: ListingStatus[]
  failedSteps: string[]
  colorClass: string
}[] = [
  {
    id: "fila",
    title: "Fila",
    statuses: ["draft"],
    failedSteps: [],          // legacy failed (no failed_step) also land here
    colorClass: "border-t-slate-400",
  },
  {
    id: "titulos",
    title: "Títulos",
    statuses: ["generating_title", "pending_title_approval"],
    failedSteps: ["generating_title", "pending_title_approval"],
    colorClass: "border-t-violet-400",
  },
  {
    id: "categoria",
    title: "Categoria & Atributos",
    statuses: ["predicting_category", "pending_seller_attributes", "pending_description"],
    failedSteps: ["predicting_category", "pending_seller_attributes", "pending_description"],
    colorClass: "border-t-blue-400",
  },
  {
    id: "imagens",
    title: "Imagens",
    statuses: ["generating_images", "pending_raw_photos", "pending_ai_engine", "pending_image_approval"],
    failedSteps: ["generating_images", "pending_image_approval"],
    colorClass: "border-t-orange-400",
  },
  {
    id: "descricao",
    title: "Descrição",
    statuses: ["generating_description", "ready_to_publish", "publishing"],
    failedSteps: ["generating_description", "ready_to_publish", "publishing"],
    colorClass: "border-t-yellow-400",
  },
  {
    id: "publicados",
    title: "Publicados",
    statuses: ["published", "published_paused"],
    failedSteps: [],
    colorClass: "border-t-green-400",
  },
]

export function PipelineBoard() {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [activeColumnId, setActiveColumnId] = useState<ColumnId | null>(null)

  const { activeSeller, isLoading: isSellerLoading } = useSeller()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["listings", activeSeller?.id],
    queryFn: () => getListings({ page_size: 200 }),
    refetchInterval: 8000,
    enabled: !!activeSeller,
  })

  const handleSelect = useCallback((columnId: ColumnId, id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
    setActiveColumnId(checked ? columnId : selectedIds.size <= 1 ? null : activeColumnId)
  }, [selectedIds, activeColumnId])

  const handleSelectAll = useCallback((columnId: ColumnId, items: ListingSummary[]) => {
    const ids = items.map((i) => i.id)
    const allSelected = ids.every((id) => selectedIds.has(id))
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (allSelected) {
        ids.forEach((id) => next.delete(id))
        setActiveColumnId(null)
      } else {
        ids.forEach((id) => next.add(id))
        setActiveColumnId(columnId)
      }
      return next
    })
  }, [selectedIds])

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set())
    setActiveColumnId(null)
  }, [])

  if (isSellerLoading || isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-slate-500" />
        <span className="ml-2 text-slate-500">Carregando anúncios...</span>
      </div>
    )
  }

  if (!activeSeller) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <p className="text-slate-600 text-sm text-center">Nenhuma conta conectada.</p>
        <Link href="/settings" className="text-sm font-medium text-primary underline underline-offset-4 hover:opacity-80">
          Ir para Configurações
        </Link>
      </div>
    )
  }

  if (error) {
    const isNoSeller = error instanceof ApiError && error.status === 422
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <p className="text-slate-600 text-sm text-center">
          {isNoSeller
            ? (error as ApiError).message
            : "Erro ao carregar anúncios. Tente recarregar a página."}
        </p>
        {isNoSeller && (
          <Link
            href="/settings"
            className="text-sm font-medium text-primary underline underline-offset-4 hover:opacity-80"
          >
            Ir para Configurações
          </Link>
        )}
      </div>
    )
  }

  const allItems: ListingSummary[] = data?.items || []

  const getColumnItems = (col: (typeof COLUMNS)[0]) =>
    allItems.filter((item) => {
      if (item.status === "failed") {
        const step = item.failed_step ?? ""
        if (!step) return col.id === "fila"
        return col.failedSteps.includes(step)
      }
      return (col.statuses as string[]).includes(item.status)
    })

  return (
    <>
      <div className="flex gap-4 overflow-x-auto pb-20 min-h-[calc(100vh-12rem)]">
        {COLUMNS.map((col) => {
          const items = getColumnItems(col)
          const allColSelected = items.length > 0 && items.every((i) => selectedIds.has(i.id))

          return (
            <div
              key={col.id}
              className={`flex-shrink-0 w-72 bg-muted/40 rounded-lg border-t-4 ${col.colorClass} border border-border p-3`}
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  {items.length > 0 && (
                    <input
                      type="checkbox"
                      checked={allColSelected}
                      onChange={() => handleSelectAll(col.id, items)}
                      className="h-4 w-4 rounded border-slate-300 cursor-pointer"
                      title="Selecionar todos"
                    />
                  )}
                  <h3 className="font-semibold text-sm text-foreground">{col.title}</h3>
                </div>
                <span className="text-xs bg-muted text-muted-foreground rounded-full px-2 py-0.5 font-medium">
                  {items.length}
                </span>
              </div>

              <div className="space-y-2">
                {items.length === 0 ? (
                  <p className="text-xs text-slate-400 text-center py-6">Nenhum anúncio</p>
                ) : (
                  items.map((listing) => (
                    <ListingCard
                      key={listing.id}
                      listing={listing}
                      selected={selectedIds.has(listing.id)}
                      onSelect={(id, checked) => handleSelect(col.id, id, checked)}
                    />
                  ))
                )}
              </div>
            </div>
          )
        })}
      </div>

      <BulkActionsBar
        selectedIds={Array.from(selectedIds)}
        activeColumnId={activeColumnId}
        onSuccess={() => {
          clearSelection()
          refetch()
        }}
        onClear={clearSelection}
      />
    </>
  )
}
