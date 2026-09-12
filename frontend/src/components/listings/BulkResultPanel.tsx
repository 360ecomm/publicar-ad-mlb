"use client"

import { AlertTriangle, CheckCircle2, X } from "lucide-react"
import type { BulkSummary } from "@/lib/bulk-actions"

export interface BulkOutcome {
  actionLabel: string
  summary: BulkSummary
}

interface Props {
  outcome: BulkOutcome
  onDismiss: () => void
}

/**
 * Resultado de uma ação em massa, legível: contagens e, para cada falha, o
 * SKU e o motivo já saneado (erro técnico nunca chega cru aqui; fica no
 * console, gravado por quem chamou `summarizeBulkResult`).
 */
export function BulkResultPanel({ outcome, onDismiss }: Props) {
  const { actionLabel, summary } = outcome
  const allOk = summary.failed === 0
  const tone = allOk
    ? "border-green-300 bg-green-50 text-green-900 dark:border-green-800 dark:bg-green-950/30 dark:text-green-200"
    : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"

  return (
    <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${tone}`} role="status">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 font-medium">
          {allOk ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
          {actionLabel}: {summary.ok} {summary.ok === 1 ? "anúncio processado" : "anúncios processados"}
          {!allOk && (
            <>
              , {summary.failed} {summary.failed === 1 ? "falhou" : "falharam"}
            </>
          )}
        </div>
        <button type="button" onClick={onDismiss} className="opacity-70 hover:opacity-100" title="Fechar">
          <X className="w-4 h-4" />
        </button>
      </div>
      {!allOk && (
        <ul className="mt-2 grid gap-0.5 text-xs">
          {summary.failures.map((f) => (
            <li key={f.listing_id} className="flex gap-2">
              <span className="font-mono font-medium shrink-0">{f.sku}</span>
              <span className="opacity-90">{f.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
