"use client"

import { useEffect, useState } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { toast } from "sonner"
import { formatDistanceToNow } from "date-fns"
import { ptBR } from "date-fns/locale"
import { Loader2, ShoppingBag, Check, ExternalLink, Plus, Unplug } from "lucide-react"
import { getDashboard, disconnectSeller, type SellerDashboardEntry } from "@/lib/api/sellers"
import { getMLConnectUrl } from "@/lib/api/auth"
import { useSeller } from "@/contexts/SellerContext"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

const STATUS_LABELS: Record<string, string> = {
  draft: "Rascunho",
  generating_title: "Gerando título",
  pending_title_approval: "Aguard. título",
  predicting_category: "Prevendo cat.",
  pending_seller_attributes: "Aguard. atributos",
  pending_description: "Aguard. descrição",
  generating_images: "Gerando imagens",
  pending_raw_photos: "Aguard. fotos",
  pending_ai_engine: "Aguard. motor de IA",
  pending_image_approval: "Aguard. imagens",
  generating_description: "Gerando descrição",
  ready_to_publish: "Pronto p/ publicar",
  publishing: "Publicando",
  published: "Publicado",
  published_paused: "Pausado",
  failed: "Falhou",
}

const STATUS_COLORS: Record<string, string> = {
  published: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  ready_to_publish: "bg-blue-100 text-blue-700",
  draft: "bg-slate-100 text-slate-600",
}

/**
 * Abre a autorização do ML em aba nova: o operador não perde a aplicação.
 * O callback do backend manda a aba nova para /contas?ml_connected=true;
 * esta aba recarrega a lista ao receber foco de novo (ver o efeito abaixo).
 */
async function openMLAuthorization(): Promise<void> {
  const url = await getMLConnectUrl()
  window.open(url, "_blank", "noopener")
}

function SellerCard({ entry, onDisconnect }: { entry: SellerDashboardEntry; onDisconnect: (e: SellerDashboardEntry) => void }) {
  const { activeSeller, setActiveSeller, sellers } = useSeller()
  const [connecting, setConnecting] = useState(false)
  const isActive = activeSeller?.id === entry.seller_id
  const seller = sellers.find((s) => s.id === entry.seller_id)
  const statusEntries = Object.entries(entry.listings_by_status).sort((a, b) => b[1] - a[1])

  const reconnect = async () => {
    setConnecting(true)
    try {
      await openMLAuthorization()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao obter URL de conexão")
    } finally {
      setConnecting(false)
    }
  }

  return (
    <Card className={isActive ? "border-green-300 ring-1 ring-green-200" : !entry.is_active ? "opacity-80" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <ShoppingBag className={`w-4 h-4 flex-shrink-0 ${entry.is_active ? "text-yellow-500" : "text-slate-400"}`} />
            <CardTitle className="text-base truncate">{entry.ml_nickname}</CardTitle>
            {isActive && (
              <span className="text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full">Ativa</span>
            )}
            {!entry.is_active && (
              <span className="text-xs font-medium text-slate-600 bg-slate-100 px-2 py-0.5 rounded-full">Desconectada</span>
            )}
          </div>
          {entry.is_active && !isActive && seller && (
            <Button variant="outline" size="sm" onClick={() => setActiveSeller(seller)}>
              <Check className="w-3.5 h-3.5 mr-1" />
              Usar
            </Button>
          )}
        </div>
        <p className="text-sm text-slate-500">
          {entry.total_listings} anúncio{entry.total_listings !== 1 ? "s" : ""}
          {entry.last_activity_at && (
            <> · última atividade {formatDistanceToNow(new Date(entry.last_activity_at), { addSuffix: true, locale: ptBR })}</>
          )}
        </p>
      </CardHeader>

      <CardContent className="space-y-3">
        {statusEntries.length === 0 ? (
          <p className="text-sm text-slate-400 italic">Nenhum anúncio ainda.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {statusEntries.map(([status, count]) => (
              <span
                key={status}
                className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full ${STATUS_COLORS[status] ?? "bg-slate-100 text-slate-600"}`}
              >
                <span>{count}</span>
                <span>{STATUS_LABELS[status] ?? status}</span>
              </span>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          {isActive && (
            <Button asChild variant="ghost" size="sm" className="px-0 text-slate-500 hover:text-foreground">
              <Link href="/listings">Ver anúncios desta conta →</Link>
            </Button>
          )}
          {entry.is_active ? (
            <Button variant="ghost" size="sm" className="ml-auto text-red-600 hover:text-red-700" onClick={() => onDisconnect(entry)}>
              <Unplug className="w-3.5 h-3.5 mr-1.5" />
              Desconectar
            </Button>
          ) : (
            <Button variant="outline" size="sm" className="ml-auto" onClick={reconnect} disabled={connecting}>
              {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5 mr-1.5" />}
              Reconectar
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default function ContasPage() {
  const queryClient = useQueryClient()
  const { activeSeller, reload } = useSeller()
  const [connecting, setConnecting] = useState(false)
  const [confirming, setConfirming] = useState<SellerDashboardEntry | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 15_000,
  })

  // Volta do OAuth (a aba nova cai aqui com ?ml_connected=true) e foco na aba
  // original: recarrega a lista de contas nos dois casos.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("ml_connected") === "true") {
      window.history.replaceState({}, "", "/contas")
      toast.success("Conta do Mercado Livre conectada.")
      void reload().then(() => queryClient.invalidateQueries({ queryKey: ["dashboard"] }))
    }
    const onFocus = () => {
      void reload().then(() => queryClient.invalidateQueries({ queryKey: ["dashboard"] }))
    }
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [reload, queryClient])

  const connect = async () => {
    setConnecting(true)
    try {
      await openMLAuthorization()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Erro ao obter URL de conexão")
    } finally {
      setConnecting(false)
    }
  }

  const disconnectMutation = useMutation({
    mutationFn: (id: string) => disconnectSeller(id),
    onSuccess: async (_seller, id) => {
      const eraAtiva = activeSeller?.id === id
      setConfirming(null)
      toast.success("Conta desconectada. O histórico foi mantido.")
      // `reload` troca a ativa (ou limpa) quando a desconectada era a ativa;
      // aí as queries precisam ser refeitas com o X-Seller-ID novo.
      await reload()
      if (eraAtiva) await queryClient.resetQueries()
      else await queryClient.invalidateQueries({ queryKey: ["dashboard"] })
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Erro ao desconectar")
    },
  })

  return (
    <div>
      <div className="mb-6 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShoppingBag className="w-5 h-5 text-slate-600" />
          <h1 className="text-2xl font-bold text-foreground">Contas</h1>
        </div>
        <Button size="sm" onClick={connect} disabled={connecting}>
          {connecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Plus className="w-4 h-4 mr-1" />Conectar conta</>}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span className="text-sm">Carregando...</span>
        </div>
      ) : !data || data.sellers.length === 0 ? (
        <div className="text-center py-16">
          <ShoppingBag className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500">Nenhuma conta conectada.</p>
          <Button className="mt-4" onClick={connect} disabled={connecting}>
            <ExternalLink className="w-4 h-4 mr-2" />
            Conectar conta Mercado Livre
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.sellers.map((entry) => (
            <SellerCard key={entry.seller_id} entry={entry} onDisconnect={setConfirming} />
          ))}
        </div>
      )}

      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="disconnect-title"
        >
          <div className="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-xl space-y-4">
            <h2 id="disconnect-title" className="text-base font-semibold">
              Desconectar {confirming.ml_nickname}?
            </h2>
            <ul className="text-sm text-slate-600 dark:text-slate-300 list-disc pl-5 space-y-1">
              <li>O histórico fica: anúncios, produtos e imagens desta conta continuam no sistema.</li>
              <li>Os anúncios desta conta param de ser publicados até reconectar.</li>
              <li>Reconectar exige autorizar de novo no Mercado Livre.</li>
            </ul>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirming(null)} disabled={disconnectMutation.isPending}>
                Cancelar
              </Button>
              <Button
                size="sm"
                className="bg-red-600 hover:bg-red-700 text-white"
                onClick={() => disconnectMutation.mutate(confirming.seller_id)}
                disabled={disconnectMutation.isPending}
              >
                {disconnectMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Desconectar"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
