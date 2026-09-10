"use client"

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { getListing, retryPipeline, deleteListing, generateImages, resumeRawPhotos, resumeAiEngine } from "@/lib/api/listings"
import { formatPrice, formatQuantity } from "@/lib/utils"
import { useRouter, useParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ListingStatusBadge } from "@/components/listings/ListingStatusBadge"
import { toast } from "sonner"
import {
  Loader2,
  ArrowLeft,
  ExternalLink,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  Trash2,
} from "lucide-react"
import Link from "next/link"
import type { ListingStatus } from "@/types/listing"
import { PROCESSING_STATUSES, STATUS_LABELS } from "@/types/listing"

const PROCESSING_MESSAGES: Partial<Record<ListingStatus, string>> = {
  generating_title: "A IA está gerando opções de título para o seu produto...",
  predicting_category: "Identificando a categoria ideal no Mercado Livre...",
  generating_images: "A IA está gerando imagens profissionais para o produto...",
  generating_description: "Criando a descrição do produto...",
  publishing: "Publicando o anúncio no Mercado Livre...",
}

export default function ListingDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const router = useRouter()
  const queryClient = useQueryClient()

  const { data: listing, isLoading, error } = useQuery({
    queryKey: ["listing", id],
    queryFn: () => getListing(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return false
      if (PROCESSING_STATUSES.includes(status) || status === "predicting_category") {
        return 4000
      }
      return false
    },
  })

  const { data: categoryName } = useQuery({
    queryKey: ["ml-category", listing?.ml_category_id],
    queryFn: async () => {
      const res = await fetch(`https://api.mercadolibre.com/categories/${listing!.ml_category_id}`)
      const data = await res.json()
      return data.name as string
    },
    enabled: !!listing?.ml_category_id,
    staleTime: Infinity,
  })

  const resumeRawPhotosMutation = useMutation({
    mutationFn: () => resumeRawPhotos(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", id] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Fotos encontradas. Geração de imagens retomada.")
    },
    onError: (err: Error) => {
      toast.error(err.message || "As fotos ainda não estão no bucket")
    },
  })

  const resumeAiEngineMutation = useMutation({
    mutationFn: () => resumeAiEngine(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", id] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Geração de imagens retomada. Se o motor continuar indisponível, o anúncio volta a esperar.")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Não foi possível retomar agora")
    },
  })

  const retryMutation = useMutation({
    mutationFn: () => retryPipeline(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", id] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Pipeline reiniciado!")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao reiniciar pipeline")
    },
  })

  const generateImagesMutation = useMutation({
    mutationFn: () => generateImages(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", id] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Gerando imagens...")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao iniciar geração de imagens")
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteListing(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Anúncio excluído")
      router.push("/listings")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao excluir anúncio")
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-slate-500" />
      </div>
    )
  }

  if (error || !listing) {
    return (
      <div className="max-w-lg mx-auto text-center py-12">
        <AlertCircle className="w-10 h-10 text-red-500 mx-auto mb-3" />
        <p className="text-slate-600">Erro ao carregar anúncio.</p>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/listings")}>
          Voltar para anúncios
        </Button>
      </div>
    )
  }

  const status = listing.status
  const isProcessing =
    PROCESSING_STATUSES.includes(status) || status === "predicting_category"

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-6">
        <Link
          href="/listings"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-foreground mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Todos os anúncios
        </Link>

        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <h1 className="text-xl font-bold text-foreground leading-snug truncate">
              {listing.selected_title || listing.sku_brand}
            </h1>
            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
              <ListingStatusBadge status={listing.status} />
              <span className="text-xs text-slate-400">
                {listing.sku_brand}
              </span>
              {listing.mlb_id && (
                <a
                  href={`https://produto.mercadolivre.com.br/${listing.mlb_id.replace(/^MLB/, "MLB-")}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
                >
                  {listing.mlb_id}
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
            </div>
          </div>

          {(status === "draft" || status === "failed") && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (confirm("Tem certeza que deseja excluir este anúncio?")) {
                  deleteMutation.mutate()
                }
              }}
              disabled={deleteMutation.isPending}
              className="text-red-600 hover:text-red-700 border-red-200 hover:border-red-300"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Status-based content */}
      {isProcessing && (
        <Card>
          <CardContent className="py-12 text-center">
            <Loader2 className="w-10 h-10 animate-spin text-purple-500 mx-auto mb-4" />
            <p className="font-medium text-foreground">
              {STATUS_LABELS[status]}
            </p>
            <p className="text-sm text-slate-500 mt-1">
              {PROCESSING_MESSAGES[status] || "Aguarde enquanto processamos seu anúncio..."}
            </p>
            <p className="text-xs text-slate-400 mt-3">Atualizando automaticamente...</p>
          </CardContent>
        </Card>
      )}

      {status === "pending_title_approval" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Título gerado pela IA</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-600 mb-4">
              A IA gerou {listing.titles.length} opção{listing.titles.length !== 1 ? "es" : ""} de título.
              Escolha o melhor para seu anúncio.
            </p>
            <Button asChild className="w-full">
              <Link href={`/listings/${id}/titles`}>Escolher título</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "pending_seller_attributes" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Atributos necessários</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-600 mb-4">
              O Mercado Livre requer informações adicionais sobre o produto para esta categoria.
            </p>
            <Button asChild className="w-full">
              <Link href={`/listings/${id}/attributes`}>Preencher atributos</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "pending_description" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">Gerar imagens do produto</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-600 mb-4">
              Categoria e atributos definidos. A IA vai gerar imagens profissionais para o anúncio.
            </p>
            <Button
              className="w-full"
              onClick={() => generateImagesMutation.mutate()}
              disabled={generateImagesMutation.isPending}
            >
              {generateImagesMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Iniciando...
                </>
              ) : (
                "Gerar imagens"
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "pending_image_approval" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">Imagens geradas</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-600 mb-4">
              A IA gerou {listing.images.length} {listing.images.length !== 1 ? "imagens" : "imagem"} para o produto.
              Selecione as que deseja usar.
            </p>
            <Button asChild className="w-full">
              <Link href={`/listings/${id}/images`}>Aprovar imagens</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "pending_raw_photos" && (
        <Card className="border-amber-200 bg-amber-50">
          <CardHeader>
            <CardTitle className="text-base text-amber-900 flex items-center gap-2">
              <AlertCircle className="w-5 h-5" />
              Aguardando fotos brutas do produto
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-amber-800 mb-3">
              As fotos deste SKU ainda não estão no bucket do seller. O sistema verifica
              sozinho a cada 15 minutos; depois de subir os arquivos, você pode verificar agora.
            </p>
            {listing.error_message && (
              <p className="text-sm text-amber-800 mb-4 p-3 bg-amber-100 rounded-md font-mono">
                {listing.error_message}
              </p>
            )}
            <Button
              className="w-full"
              disabled={resumeRawPhotosMutation.isPending}
              onClick={() => resumeRawPhotosMutation.mutate()}
            >
              {resumeRawPhotosMutation.isPending ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : null}
              Verificar fotos agora
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "pending_ai_engine" && (
        <Card className="border-amber-200 bg-amber-50">
          <CardHeader>
            <CardTitle className="text-base text-amber-900 flex items-center gap-2">
              <AlertCircle className="w-5 h-5" />
              Aguardando o motor de imagem (crédito OpenAI)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-amber-800 mb-3">
              O motor de imagem respondeu como indisponível: crédito esgotado, chave inválida
              ou instabilidade. O sistema tenta de novo sozinho a cada 15 minutos; depois de
              recarregar o crédito, você pode tentar agora.
            </p>
            {listing.error_message && (
              <p className="text-sm text-amber-800 mb-4 p-3 bg-amber-100 rounded-md font-mono">
                {listing.error_message}
              </p>
            )}
            <Button
              className="w-full"
              disabled={resumeAiEngineMutation.isPending}
              onClick={() => resumeAiEngineMutation.mutate()}
            >
              {resumeAiEngineMutation.isPending ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : null}
              Tentar agora
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "ready_to_publish" && (
        <Card className="border-blue-200 bg-blue-50">
          <CardHeader>
            <CardTitle className="text-base text-blue-900">Pronto para publicar</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-blue-700 mb-4">
              Tudo certo! Revise as informações e publique seu anúncio no Mercado Livre.
            </p>
            <Button asChild className="w-full">
              <Link href={`/listings/${id}/preview`}>Revisar e publicar</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {status === "published" && (
        <Card className="border-green-200 bg-green-50">
          <CardContent className="py-8 text-center">
            <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-3" />
            <p className="font-semibold text-green-900 text-lg">Anúncio publicado!</p>
            {listing.mlb_id && (
              <a
                href={`https://produto.mercadolivre.com.br/${listing.mlb_id.replace(/^MLB/, "MLB-")}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-blue-600 hover:underline text-sm mt-3"
              >
                Ver no Mercado Livre: {listing.mlb_id}
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            )}
          </CardContent>
        </Card>
      )}

      {status === "failed" && (
        <Card className="border-red-200 bg-red-50">
          <CardHeader>
            <CardTitle className="text-base text-red-900 flex items-center gap-2">
              <AlertCircle className="w-5 h-5" />
              Erro no pipeline
            </CardTitle>
          </CardHeader>
          <CardContent>
            {listing.error_message && (
              <p className="text-sm text-red-700 mb-4 p-3 bg-red-100 rounded-md font-mono">
                {listing.error_message}
              </p>
            )}
            <Button
              onClick={() => retryMutation.mutate()}
              disabled={retryMutation.isPending}
              className="w-full"
            >
              {retryMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Reiniciando...
                </>
              ) : (
                <>
                  <RefreshCw className="w-4 h-4 mr-2" />
                  Tentar novamente
                </>
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Product details card */}
      <Card className="mt-4">
        <CardHeader>
          <CardTitle className="text-sm text-slate-600 font-medium">Detalhes do produto</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Marca</span>
            <span className="font-medium">{listing.sku_brand}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Preço</span>
            <span className="font-medium">R$ {formatPrice(listing.price)}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Estoque</span>
            <span className="font-medium">{formatQuantity(listing.stock_quantity)} unidades</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-slate-500">Condição</span>
            <span className="font-medium">
              {listing.condition === "new" ? "Novo" : "Usado"}
            </span>
          </div>
          {listing.ml_category_id && (
            <div className="flex justify-between text-sm gap-4">
              <span className="text-slate-500 shrink-0">Categoria ML</span>
              <span className="font-medium text-right">
                {categoryName ?? listing.ml_category_id}
                <span className="block font-mono text-xs text-slate-400">{listing.ml_category_id}</span>
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Jobs history */}
      {listing.jobs && listing.jobs.length > 0 && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle className="text-sm text-slate-600 font-medium">Histórico de jobs</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {listing.jobs.map((job) => (
                <div
                  key={job.id}
                  className="flex items-start justify-between text-xs py-1.5 border-b border-slate-100 last:border-0"
                >
                  <div>
                    <span className="font-mono text-slate-600">{job.job_type}</span>
                    {job.error_message && (
                      <p className="text-red-500 mt-0.5">{job.error_message}</p>
                    )}
                  </div>
                  <span
                    className={`ml-2 flex-shrink-0 ${
                      job.status === "success"
                        ? "text-green-600"
                        : job.status === "failure"
                          ? "text-red-600"
                          : "text-slate-400"
                    }`}
                  >
                    {job.status}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
