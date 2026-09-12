"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, CheckSquare, ImageOff, Loader2, Square } from "lucide-react"
import { approveImages } from "@/lib/api/listings"
import {
  approvalPlan,
  buildGallerySlots,
  candidateAlternatives,
  defaultSelection,
  mlPictureUrl,
  orderedApprovedIds,
  type GallerySlot,
} from "@/lib/image-review"
import { useReviewTimer } from "@/hooks/useReviewTimer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { ImageOut } from "@/types/listing"

interface Props {
  listingId: string
  images: ImageOut[]
}

/**
 * Galeria de revisão por POSIÇÃO (esquema de 5 posições).
 *
 * Reescrita da galeria "selecione as que quiser": aqui as 5 posições aparecem
 * sempre, na mesma ordem e com o rótulo da posição; candidatas das Frentes A/B
 * nunca entram (`is_candidate` vem do backend); a seleção nasce com tudo que
 * está pronto marcado; e os ids são enviados em ordem de posição, nunca na
 * ordem dos cliques (ver `orderedApprovedIds` em lib/image-review.ts).
 */
export function ImageGallery({ listingId, images }: Props) {
  const router = useRouter()
  const queryClient = useQueryClient()

  const slots = useMemo(() => buildGallerySlots(images), [images])
  const alternativas = useMemo(() => candidateAlternatives(images), [images])

  // Seleção inicial = todas as posições prontas. `useState` com função só roda
  // na montagem, que é o que se quer: o operador parte do caso normal (5
  // marcadas) e desmarca o que não serve, em vez de clicar 5 vezes.
  const [selected, setSelected] = useState<Set<string>>(() => new Set(defaultSelection(slots)))
  const [confirming, setConfirming] = useState(false)

  const plan = useMemo(() => approvalPlan(slots, selected), [slots, selected])
  // Conta a partir do momento em que há imagem na tela; pausa com a aba fora de foco.
  const elapsedSeconds = useReviewTimer(slots.some((s) => s.state !== "missing"))

  const mutation = useMutation({
    mutationFn: () => {
      // ORDEM = posição. `approve_images` renumera os slots na ordem em que os
      // ids chegam, e essa ordem é a publicada no ML (CLAUDE.md, "A aprovação
      // individual renumera; a em massa não"). Nunca `Array.from(selected)`:
      // um Set guarda a ordem dos cliques, e remarcar a capa por último a
      // mandaria para o fim da galeria.
      const ids = orderedApprovedIds(slots, selected)
      return approveImages(listingId, ids, elapsedSeconds())
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      queryClient.invalidateQueries({ queryKey: ["status-counts"] })
      toast.success(`Imagens aprovadas: ${plan.total} no anúncio.`)
      router.push("/listings")
    },
    onError: (err: Error) => {
      setConfirming(false)
      toast.error(err.message || "Erro ao aprovar imagens")
    },
  })

  const toggle = (slot: GallerySlot) => {
    if (slot.state !== "ready" || slot.image === null) return
    const id = slot.image.id
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleApprove = () => {
    if (!plan.canApprove) return
    // Aprovar tudo que está disponível: um clique. Aprovar menos: confirma,
    // porque a consequência (anúncio com menos imagens) importa.
    if (plan.needsConfirmation) setConfirming(true)
    else mutation.mutate()
  }

  const ausentes = slots.every((s) => s.state === "missing")

  return (
    <div className="space-y-6">
      {ausentes && (
        <p className="text-sm text-slate-500">Nenhuma imagem gerada ainda para este anúncio.</p>
      )}

      {(alternativas.cover || alternativas.specs) && (
        <p className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Existe alternativa gerada sob demanda para{" "}
          {[alternativas.cover && "a capa", alternativas.specs && "a ficha técnica"].filter(Boolean).join(" e ")}.
          A escolha entre elas fica para a próxima etapa da tela; esta galeria mostra só as posições oficiais.
        </p>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        {slots.map((slot) => (
          <SlotCard
            key={slot.position}
            slot={slot}
            selected={slot.image !== null && selected.has(slot.image.id)}
            onToggle={() => toggle(slot)}
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <p className="text-sm text-slate-600">
          {plan.blockedByGenerating ? (
            <span className="text-amber-700">
              Há posição em regeneração; a aprovação fica disponível quando ela terminar.
            </span>
          ) : plan.available === 0 ? (
            "Nenhuma posição pronta para aprovar."
          ) : (
            <>
              <span className="font-medium text-foreground">{plan.total}</span> de {plan.available}{" "}
              {plan.available === 1 ? "posição pronta" : "posições prontas"} selecionada
              {plan.total === 1 ? "" : "s"}
              {plan.excludedLabels.length > 0 && (
                <span className="ml-2 text-amber-700">· fora: {plan.excludedLabels.join(", ")}</span>
              )}
            </>
          )}
        </p>
        <Button onClick={handleApprove} disabled={!plan.canApprove || mutation.isPending}>
          {mutation.isPending ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Aprovando...
            </>
          ) : (
            `Aprovar ${plan.total} ${plan.total === 1 ? "imagem" : "imagens"}`
          )}
        </Button>
      </div>

      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="approve-confirm-title"
        >
          <div className="w-full max-w-md rounded-lg bg-background p-5 shadow-lg">
            <h2 id="approve-confirm-title" className="text-base font-semibold">
              Aprovar com menos imagens?
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              O anúncio terá <span className="font-medium text-foreground">{plan.total}</span>{" "}
              {plan.total === 1 ? "imagem" : "imagens"}, de {plan.available} disponíveis.
            </p>
            <p className="mt-1 text-sm text-slate-600">
              Fica{plan.excludedLabels.length === 1 ? "" : "m"} de fora:{" "}
              <span className="font-medium text-foreground">{plan.excludedLabels.join(", ")}</span>.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirming(false)} disabled={mutation.isPending}>
                Voltar
              </Button>
              <Button size="sm" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
                {mutation.isPending ? "Aprovando..." : `Aprovar ${plan.total}`}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const STATE_TEXT: Record<Exclude<GallerySlot["state"], "ready">, string> = {
  qa_failed: "Reprovada no controle de qualidade",
  generation_failed: "Falhou ao gerar",
  generating: "Regenerando…",
  missing: "Não gerada",
}

function SlotCard({
  slot,
  selected,
  onToggle,
}: {
  slot: GallerySlot
  selected: boolean
  onToggle: () => void
}) {
  const pronta = slot.state === "ready" && slot.image?.ml_picture_id
  const clicavel = slot.state === "ready"

  return (
    <div
      role={clicavel ? "checkbox" : undefined}
      aria-checked={clicavel ? selected : undefined}
      aria-label={`${slot.label}: ${slot.state === "ready" ? (selected ? "marcada" : "desmarcada") : STATE_TEXT[slot.state]}`}
      tabIndex={clicavel ? 0 : -1}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (clicavel && (e.key === " " || e.key === "Enter")) {
          e.preventDefault()
          onToggle()
        }
      }}
      className={cn(
        "relative overflow-hidden rounded-lg border-2 transition-all",
        clicavel && "cursor-pointer",
        clicavel && selected && "border-blue-500 shadow-md",
        clicavel && !selected && "border-slate-200 hover:border-slate-300",
        !clicavel && "border-dashed border-slate-200",
        slot.state === "missing" && "opacity-60",
      )}
    >
      <div className="relative aspect-square bg-slate-100">
        {pronta ? (
          // Imagem externa do CDN do ML, já no tamanho final (1200x1200): o
          // otimizador do next/image não acrescenta nada aqui, e exigiria
          // liberar o domínio na configuração.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={mlPictureUrl(slot.image!.ml_picture_id!)}
            alt={`${slot.label} do anúncio`}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-3 text-center text-sm text-slate-500">
            {slot.state === "generating" ? (
              <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
            ) : (
              <ImageOff className="h-6 w-6 text-slate-300" />
            )}
            <span className={cn(slot.state === "missing" && "text-slate-400")}>
              {slot.state === "ready" ? "Sem prévia" : STATE_TEXT[slot.state]}
            </span>
            {slot.reason && (
              <span className="text-xs leading-snug text-red-700" title={slot.reason}>
                {slot.reason}
              </span>
            )}
          </div>
        )}

        {clicavel && (
          <div className="absolute right-2 top-2">
            {selected ? (
              <CheckSquare className="h-6 w-6 rounded bg-white text-blue-500" />
            ) : (
              <Square className="h-6 w-6 rounded bg-white text-slate-400" />
            )}
          </div>
        )}
        {clicavel && selected && <div className="pointer-events-none absolute inset-0 bg-blue-500/10" />}
      </div>

      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        <span className="text-sm font-medium text-foreground">
          <span className="mr-1 font-mono text-xs text-slate-400">{slot.position + 1}</span>
          {slot.label}
        </span>
        {slot.coverOrigin && (
          <span
            className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500"
            title={slot.coverOrigin === "ia" ? "Capa gerada pela IA" : "Recorte da foto original, sem IA"}
          >
            {slot.coverOrigin === "ia" ? "IA" : "Recorte"}
          </span>
        )}
      </div>
    </div>
  )
}
