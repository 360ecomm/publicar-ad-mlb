"use client"

import { useQuery } from "@tanstack/react-query"
import { useParams } from "next/navigation"
import Link from "next/link"
import { ArrowLeft, Loader2 } from "lucide-react"
import { getListing } from "@/lib/api/listings"
import { ImageGallery } from "@/components/listings/ImageGallery"
import { ListingStatusBadge } from "@/components/listings/ListingStatusBadge"

interface MlCategoryNode {
  id: string
  name: string
}

/**
 * Caminho completo da categoria pela API pública do ML — mesmo padrão da tela
 * de detalhe (`listings/[id]/page.tsx`), que busca `name`; aqui interessa
 * `path_from_root`, porque a revisão de imagens é o primeiro ponto em que um
 * humano pode pegar um erro de categoria (ex.: "body splash" indo parar em
 * Águas Minerais).
 */
async function fetchCategoryPath(categoryId: string): Promise<string[]> {
  const res = await fetch(`https://api.mercadolibre.com/categories/${categoryId}`)
  if (!res.ok) throw new Error(`categoria ${categoryId}: HTTP ${res.status}`)
  const data = (await res.json()) as { name?: string; path_from_root?: MlCategoryNode[] }
  const path = (data.path_from_root ?? []).map((n) => n.name).filter(Boolean)
  return path.length > 0 ? path : data.name ? [data.name] : []
}

export default function ImagesPage() {
  const params = useParams<{ id: string }>()
  const id = params.id

  const { data: listing, isLoading } = useQuery({
    queryKey: ["listing", id],
    queryFn: () => getListing(id),
  })

  const { data: categoryPath, isError: categoryError } = useQuery({
    queryKey: ["ml-category-path", listing?.ml_category_id],
    queryFn: () => fetchCategoryPath(listing!.ml_category_id!),
    enabled: !!listing?.ml_category_id,
    staleTime: Infinity,
  })

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
      </div>
    )
  }

  if (!listing) return null

  const titulo = listing.selected_title || listing.sku_description || listing.sku_brand

  return (
    <div className="w-full">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-3 flex items-center gap-4 text-sm">
            <Link
              href="/listings"
              className="inline-flex items-center gap-1 text-slate-500 hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
              Fila
            </Link>
            <Link href={`/listings/${id}`} className="text-slate-500 hover:text-foreground">
              Detalhe do anúncio
            </Link>
          </div>
          <h1 className="text-2xl font-bold text-foreground">Revisar imagens</h1>
          <p className="mt-1 text-sm text-slate-500">
            As 5 posições do anúncio, na ordem em que serão publicadas. Desmarque o que não serve.
          </p>
        </div>
        <ListingStatusBadge status={listing.status} />
      </div>

      <dl className="mb-6 grid grid-cols-1 gap-x-8 gap-y-3 rounded-lg border bg-card p-4 text-sm md:grid-cols-[auto_1fr]">
        <dt className="text-slate-500">SKU</dt>
        <dd className="font-mono text-foreground">{listing.sku_external_id ?? "—"}</dd>

        <dt className="text-slate-500">Título</dt>
        <dd className="min-w-0 break-words font-medium text-foreground">
          {titulo}
          {!listing.selected_title && (
            <span className="ml-2 text-xs font-normal text-slate-400">(descrição do catálogo; título ainda não escolhido)</span>
          )}
        </dd>

        <dt className="text-slate-500">Categoria</dt>
        <dd className="min-w-0 text-foreground">
          {listing.ml_category_id ? (
            <>
              <span className="break-words">
                {categoryPath && categoryPath.length > 0
                  ? categoryPath.join(" › ")
                  : categoryError
                    ? "caminho indisponível no momento"
                    : "carregando caminho…"}
              </span>
              <span className="ml-2 font-mono text-xs text-slate-400">{listing.ml_category_id}</span>
            </>
          ) : (
            "—"
          )}
        </dd>
      </dl>

      <ImageGallery listingId={id} images={listing.images} />
    </div>
  )
}
