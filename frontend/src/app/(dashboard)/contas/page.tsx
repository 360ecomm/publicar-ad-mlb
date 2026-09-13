"use client"

import { useQuery } from "@tanstack/react-query"
import { getDashboard, type SellerDashboardEntry } from "@/lib/api/sellers"
import { useSeller } from "@/contexts/SellerContext"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Loader2, ShoppingBag, Check } from "lucide-react"
import Link from "next/link"
import { formatDistanceToNow } from "date-fns"
import { ptBR } from "date-fns/locale"

const STATUS_LABELS: Record<string, string> = {
  draft: "Rascunho",
  generating_title: "Gerando título",
  pending_title_approval: "Aguard. título",
  predicting_category: "Prevendo cat.",
  pending_seller_attributes: "Aguard. atributos",
  pending_description: "Aguard. descrição",
  generating_images: "Gerando imagens",
  pending_image_approval: "Aguard. imagens",
  generating_description: "Gerando descrição",
  ready_to_publish: "Pronto p/ publicar",
  publishing: "Publicando",
  published: "Publicado",
  failed: "Falhou",
}

const STATUS_COLORS: Record<string, string> = {
  published: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  ready_to_publish: "bg-blue-100 text-blue-700",
  draft: "bg-slate-100 text-slate-600",
}

function SellerCard({ entry }: { entry: SellerDashboardEntry }) {
  const { activeSeller, setActiveSeller, sellers } = useSeller()
  const isActive = activeSeller?.id === entry.seller_id
  const seller = sellers.find((s) => s.id === entry.seller_id)

  const handleActivate = () => {
    if (seller) setActiveSeller(seller)
  }

  const statusEntries = Object.entries(entry.listings_by_status).sort((a, b) => b[1] - a[1])

  return (
    <Card className={isActive ? "border-green-300 ring-1 ring-green-200" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <ShoppingBag className="w-4 h-4 text-yellow-500 flex-shrink-0" />
            <CardTitle className="text-base">{entry.ml_nickname}</CardTitle>
            {isActive && (
              <span className="text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full">
                Ativa
              </span>
            )}
          </div>
          {!isActive && seller && (
            <Button variant="outline" size="sm" onClick={handleActivate}>
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

      <CardContent>
        {statusEntries.length === 0 ? (
          <p className="text-sm text-slate-400 italic">Nenhum anúncio ainda.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {statusEntries.map(([status, count]) => (
              <span
                key={status}
                className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full ${
                  STATUS_COLORS[status] ?? "bg-slate-100 text-slate-600"
                }`}
              >
                <span>{count}</span>
                <span>{STATUS_LABELS[status] ?? status}</span>
              </span>
            ))}
          </div>
        )}

        {isActive && (
          <Button asChild variant="ghost" size="sm" className="mt-3 px-0 text-slate-500 hover:text-foreground">
            <Link href="/listings">Ver anúncios desta conta →</Link>
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    refetchInterval: 15_000,
  })

  return (
    <div>
      <div className="mb-6 flex items-center gap-2">
        <ShoppingBag className="w-5 h-5 text-slate-600" />
        <h1 className="text-2xl font-bold text-foreground">Contas</h1>
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
          <Button asChild className="mt-4">
            <Link href="/settings">Conectar conta ML</Link>
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.sellers.map((entry) => (
            <SellerCard key={entry.seller_id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}
