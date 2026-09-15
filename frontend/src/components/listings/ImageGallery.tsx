"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, CheckSquare, ImageOff, Images, Loader2, RefreshCw, Square } from "lucide-react"
import { approveImages, getListings, regeneratePosition } from "@/lib/api/listings"
import { ApiError } from "@/lib/api/client"
import {
  approvalPlan,
  approvalToast,
  buildGallerySlots,
  candidateAlternatives,
  canRegenerate,
  defaultSelection,
  describeRegenerateError,
  mlPictureUrl,
  nextReviewRoute,
  orderedApprovedIds,
  regenerateWarning,
  type GalleryPosition,
  type GallerySlot,
} from "@/lib/image-review"
import { useReviewTimer } from "@/hooks/useReviewTimer"
import { RawPhotosPanel } from "@/components/listings/RawPhotosPanel"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { ImageOut, ListingDetail, ListingStatus } from "@/types/listing"

interface Props {
  listingId: string
  /** SKU do anúncio, para o aviso "SKU X: n imagens aprovadas". */
  sku: string | null
  /**
   * Status do anúncio: decide se a posição APROVADA pode ser regenerada.
   * Em `ready_to_publish` o backend aceita (desaprova a posição e devolve o
   * anúncio à revisão); em `pending_image_approval` recusa com 409.
   */
  status: ListingStatus
  images: ImageOut[]
}

/**
 * Galeria de revisão por POSIÇÃO (esquema de 5 posições).
 *
 * As 5 posições aparecem sempre, na mesma ordem e com o rótulo da posição;
 * candidatas das Frentes A/B nunca entram (`is_candidate` vem do backend); a
 * seleção nasce com tudo que está pronto marcado; e os ids são enviados em
 * ordem de posição, nunca na ordem dos cliques (`orderedApprovedIds`).
 *
 * Parte 2: regenerar uma posição que não serviu (sem prender o operador: a
 * posição vira `generating` e a tela segue usável; quem atualiza é a consulta
 * periódica da página, só enquanto houver `generating`), ver as fotos
 * originais sob demanda, e ir direto ao próximo anúncio depois de aprovar.
 */
export function ImageGallery({ listingId, sku, status, images }: Props) {
  const router = useRouter()
  const queryClient = useQueryClient()

  const slots = useMemo(() => buildGallerySlots(images), [images])
  const alternativas = useMemo(() => candidateAlternatives(images), [images])

  // A seleção é guardada pelo AVESSO: o que o operador DESMARCOU. Assim uma
  // posição que chega pronta depois (regeneração concluída durante a revisão)
  // entra marcada, como as outras, sem efeito nem sincronização de estado.
  const [deselected, setDeselected] = useState<Set<string>>(() => new Set())
  const selected = useMemo(
    () => new Set(defaultSelection(slots).filter((id) => !deselected.has(id))),
    [slots, deselected],
  )
  const [confirming, setConfirming] = useState(false)
  const [confirmingRegen, setConfirmingRegen] = useState<GalleryPosition | null>(null)
  const [showOriginals, setShowOriginals] = useState(false)

  const plan = useMemo(() => approvalPlan(slots, selected), [slots, selected])
  const generatingLabels = slots.filter((s) => s.state === "generating").map((s) => s.label)
  // Conta a partir do momento em que há imagem na tela; pausa com a aba fora de foco.
  const elapsedSeconds = useReviewTimer(slots.some((s) => s.state !== "missing"))

  // Só consulta a fila DEPOIS de aprovar, e na hora: com workers rodando, uma
  // lista carregada antes envelhece. `enabled: false` = nunca sozinha.
  const proximoQuery = useQuery({
    queryKey: ["listings", "next-review", listingId],
    // Dois itens, não um: o atual pode ainda aparecer na resposta (a fila é
    // lida logo depois do commit) e excluí-lo não pode deixar a lista vazia.
    queryFn: () => getListings({ status: ["pending_image_approval"], page_size: 2 }),
    enabled: false,
    gcTime: 0,
  })

  const mutation = useMutation({
    mutationFn: () => {
      // ORDEM = posição. `approve_images` renumera os slots na ordem em que os
      // ids chegam, e essa ordem é a publicada no ML (CLAUDE.md, "A aprovação
      // individual renumera; a em massa não"). Nunca `Array.from(selected)`.
      const ids = orderedApprovedIds(slots, selected)
      return approveImages(listingId, ids, elapsedSeconds())
    },
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      queryClient.invalidateQueries({ queryKey: ["status-counts"] })
      // Aviso no canto, sem clique e sem bloquear: o operador já está indo embora.
      toast.success(approvalToast(sku, plan.total))
      let destino = "/listings"
      try {
        const { data } = await proximoQuery.refetch()
        destino = nextReviewRoute(data?.items ?? [], listingId)
      } catch {
        // Falhou a consulta da fila: a aprovação já aconteceu; volta para a fila.
      }
      router.push(destino)
    },
    onError: (err: Error) => {
      setConfirming(false)
      toast.error(err.message || "Erro ao aprovar imagens")
    },
  })

  const regen = useMutation({
    mutationFn: (position: GalleryPosition) => regeneratePosition(listingId, position),
    onSuccess: (placeholder) => {
      // O 202 já traz o placeholder `generating`: entra no cache na hora, a
      // posição muda de estado e a consulta periódica da página (só enquanto
      // houver `generating`) traz o resultado. Nada prende o operador aqui.
      queryClient.setQueryData<ListingDetail>(["listing", listingId], (old) =>
        old ? { ...old, images: [...old.images, placeholder] } : old,
      )
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
    },
    onError: (err: Error, position) => {
      const status = err instanceof ApiError ? err.status : 0
      toast.error(describeRegenerateError(status, err.message, position))
      // 409 = o estado da tela envelheceu; a consulta mostra o que há de fato.
      if (status === 409) queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
    },
    onSettled: () => setConfirmingRegen(null),
  })

  const toggle = (slot: GallerySlot) => {
    if (slot.state !== "ready" || slot.image === null) return
    const id = slot.image.id
    setDeselected((prev) => {
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

  const handleRegenerate = (slot: GallerySlot) => {
    if (!canRegenerate(slot, status) || regen.isPending) return
    // Dois motivos de aviso, que podem valer juntos: Benefícios (posição 2)
    // pede copy nova ao LLM, e a partir de `ready_to_publish` regenerar
    // desfaz a aprovação e devolve o anúncio à revisão. O operador precisa
    // saber ANTES de gastar a chamada.
    if (regenerateWarning(slot.position, status)) setConfirmingRegen(slot.position)
    else regen.mutate(slot.position)
  }

  const ausentes = slots.every((s) => s.state === "missing")
  const regenAviso = confirmingRegen === null ? null : regenerateWarning(confirmingRegen, status)

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
            listingStatus={status}
            selected={slot.image !== null && selected.has(slot.image.id)}
            onToggle={() => toggle(slot)}
            onRegenerate={() => handleRegenerate(slot)}
            regenerating={regen.isPending && regen.variables === slot.position}
            regenDisabled={regen.isPending}
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => setShowOriginals((v) => !v)}>
          <Images className="mr-2 h-4 w-4" />
          {showOriginals ? "Ocultar original" : "Ver original"}
        </Button>
        <span className="text-xs text-slate-500">
          As fotos brutas do produto, para comparar na dúvida. Buscadas só quando você pede.
        </span>
      </div>
      {showOriginals && <RawPhotosPanel listingId={listingId} />}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <p className="text-sm text-slate-600">
          {plan.blockedByGenerating ? (
            <span className="flex items-center gap-2 text-amber-700">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              Aprovar bloqueado enquanto {generatingLabels.join(" e ")}{" "}
              {generatingLabels.length === 1 ? "está sendo regenerada" : "estão sendo regeneradas"}: a tela
              atualiza sozinha quando terminar. Você pode sair; o anúncio continua na fila.
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

      {confirmingRegen !== null && regenAviso && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="regen-confirm-title"
        >
          <div className="w-full max-w-md rounded-lg bg-background p-5 shadow-lg">
            <h2 id="regen-confirm-title" className="text-base font-semibold">
              Regenerar {slots[confirmingRegen].label}?
            </h2>
            <p className="mt-2 flex items-start gap-2 text-sm text-amber-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              {regenAviso.long}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmingRegen(null)} disabled={regen.isPending}>
                Voltar
              </Button>
              <Button size="sm" onClick={() => regen.mutate(confirmingRegen)} disabled={regen.isPending}>
                {regen.isPending ? "Enviando..." : "Regenerar"}
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
  listingStatus,
  selected,
  onToggle,
  onRegenerate,
  regenerating,
  regenDisabled,
}: {
  slot: GallerySlot
  /** Ver `canRegenerate`: a posição aprovada só regenera em `ready_to_publish`. */
  listingStatus: ListingStatus
  selected: boolean
  onToggle: () => void
  onRegenerate: () => void
  /** Este slot está com o pedido de regeneração em voo (antes do 202). */
  regenerating: boolean
  /** Algum pedido em voo: um por vez, para não enfileirar por engano. */
  regenDisabled: boolean
}) {
  const pronta = slot.state === "ready" && slot.image?.ml_picture_id
  const clicavel = slot.state === "ready"
  const regeneravel = canRegenerate(slot, listingStatus)
  const regenAviso = regenerateWarning(slot.position, listingStatus)

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border-2 transition-all",
        clicavel && selected && "border-blue-500 shadow-md",
        clicavel && !selected && "border-slate-200 hover:border-slate-300",
        !clicavel && "border-dashed border-slate-200",
        slot.state === "missing" && "opacity-80",
      )}
    >
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
        className={cn("relative aspect-square bg-slate-100", clicavel && "cursor-pointer")}
      >
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

      {regeneravel && (
        <div className="border-t px-2 py-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full"
            onClick={onRegenerate}
            disabled={regenDisabled}
            aria-label={`Regenerar ${slot.label}`}
          >
            {regenerating ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-2 h-3.5 w-3.5" />}
            Regenerar
          </Button>
          {regenAviso && (
            <p className="mt-1 text-[11px] leading-snug text-amber-700">{regenAviso.short}</p>
          )}
        </div>
      )}
    </div>
  )
}
