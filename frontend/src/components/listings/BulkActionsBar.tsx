"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { AlertTriangle, Loader2, X } from "lucide-react"
import {
  bulkApproveImages,
  bulkApproveTitles,
  bulkGenerateImages,
  bulkPublish,
  bulkRejectTitles,
  bulkStartPipeline,
} from "@/lib/api/listings"
import { bulkAvailabilityFor, summarizeBulkResult, type BulkActionId, type BulkActionSpec, type BulkSummary } from "@/lib/bulk-actions"
import { STATUS_LABELS, type BulkResult, type ListingStatus } from "@/types/listing"
import { Button } from "@/components/ui/button"

/** O que a fila guarda de cada selecionado: sobrevive à paginação e ao filtro. */
export interface SelectedListing {
  id: string
  sku: string | null
  status: ListingStatus
}

interface Props {
  selected: SelectedListing[]
  /** Quantos selecionados não estão na página visível. */
  offPageCount: number
  onClear: () => void
  /** Chamado depois de qualquer ação em massa, com o resumo já saneado. */
  onDone: (summary: BulkSummary, actionLabel: string) => void
}

const RUNNERS: Record<Exclude<BulkActionId, "fill_attributes">, (ids: string[]) => Promise<BulkResult>> = {
  start_pipeline: bulkStartPipeline,
  approve_titles: bulkApproveTitles,
  reject_titles: bulkRejectTitles,
  generate_images: bulkGenerateImages,
  approve_images: bulkApproveImages,
  publish: bulkPublish,
}

const TONE_CLASS = {
  primary: "bg-green-600 hover:bg-green-500 text-white",
  danger: "bg-red-600 hover:bg-red-500 text-white",
  neutral: "bg-slate-600 hover:bg-slate-500 text-white",
} as const

export function BulkActionsBar({ selected, offPageCount, onClear, onDone }: Props) {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [confirming, setConfirming] = useState<BulkActionSpec | null>(null)

  if (selected.length === 0) return null

  const availability = bulkAvailabilityFor(selected.map((s) => s.status))
  const skuOf = (id: string) => selected.find((s) => s.id === id)?.sku

  const execute = async (action: BulkActionSpec) => {
    if (action.href) {
      router.push(action.href)
      return
    }
    setConfirming(null)
    setLoading(true)
    try {
      const result = await RUNNERS[action.id as Exclude<BulkActionId, "fill_attributes">](selected.map((s) => s.id))
      onDone(summarizeBulkResult(result, skuOf), action.label)
    } catch (err) {
      console.error("[bulk] falha na chamada em massa", action.id, err)
      onDone(
        {
          ok: 0,
          failed: selected.length,
          failures: selected.map((s) => ({
            listing_id: s.id,
            sku: s.sku || s.id,
            message: "a chamada em massa falhou antes de processar os itens; tente de novo",
            technical: true,
            raw: err instanceof Error ? err.message : String(err),
          })),
        },
        action.label
      )
    } finally {
      setLoading(false)
    }
  }

  const onAction = (action: BulkActionSpec) => {
    if (action.confirm) setConfirming(action)
    else void execute(action)
  }

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 flex justify-center pb-4 pointer-events-none">
      <div className="pointer-events-auto bg-slate-900 text-white rounded-xl shadow-2xl px-5 py-3 flex items-center gap-4 min-w-[420px] max-w-3xl">
        <div className="flex-1 text-sm leading-tight">
          <div>
            <span className="font-semibold">{selected.length}</span>{" "}
            <span className="text-slate-300">
              {selected.length === 1 ? "anúncio selecionado" : "anúncios selecionados"}
            </span>
            {availability.kind === "actions" || availability.kind === "none" ? (
              <span className="text-slate-400 ml-1">· {STATUS_LABELS[availability.status]}</span>
            ) : null}
          </div>
          {offPageCount > 0 && (
            <div className="text-xs text-slate-400">
              {offPageCount} {offPageCount === 1 ? "está" : "estão"} fora da página visível
            </div>
          )}
          {availability.kind === "mixed" && (
            <div className="text-xs text-amber-300 flex items-center gap-1 mt-0.5">
              <AlertTriangle className="w-3 h-3" />
              Selecione anúncios no mesmo estágio para agir em massa
              <span className="text-slate-400">
                ({availability.statuses.map((s) => STATUS_LABELS[s]).join(", ")})
              </span>
            </div>
          )}
          {availability.kind === "none" && (
            <div className="text-xs text-slate-400 mt-0.5">Nenhuma ação em massa para este estágio</div>
          )}
        </div>

        {loading && <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}

        {!loading &&
          availability.kind === "actions" &&
          availability.actions.map((action) => (
            <button
              key={action.id}
              type="button"
              onClick={() => onAction(action)}
              className={`text-sm font-medium px-3 py-1.5 rounded-lg transition-colors ${TONE_CLASS[action.tone]}`}
            >
              {action.label}
            </button>
          ))}

        <button
          type="button"
          onClick={onClear}
          disabled={loading}
          className="text-slate-400 hover:text-white transition-colors ml-1 disabled:opacity-50"
          title="Cancelar seleção"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {confirming && (
        <div
          className="pointer-events-auto fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="bulk-confirm-title"
        >
          <div className="bg-card text-foreground rounded-xl shadow-2xl border border-border max-w-md w-full p-5">
            <h2 id="bulk-confirm-title" className="text-base font-semibold">
              Publicar {selected.length} {selected.length === 1 ? "anúncio" : "anúncios"} no Mercado Livre?
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              A publicação é irreversível: o anúncio vai ao ar na conta conectada.
            </p>
            <ul className="mt-3 max-h-48 overflow-y-auto rounded border border-border divide-y divide-border text-sm">
              {selected.map((s) => (
                <li key={s.id} className="px-3 py-1.5 font-mono text-xs">
                  {s.sku || s.id}
                </li>
              ))}
            </ul>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" size="sm" onClick={() => setConfirming(null)}>
                Cancelar
              </Button>
              <Button size="sm" onClick={() => void execute(confirming)}>
                Publicar {selected.length}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
