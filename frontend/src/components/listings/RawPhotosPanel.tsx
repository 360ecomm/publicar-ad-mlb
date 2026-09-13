"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { AlertCircle, Loader2 } from "lucide-react"
import { getRawPhotos } from "@/lib/api/listings"
import { rawPhotosView } from "@/lib/image-review"

interface Props {
  listingId: string
}

/**
 * Fotos brutas do produto, sob demanda (botão "Ver original" da revisão).
 * Buscadas UMA vez por anúncio (`staleTime: Infinity`) e reaproveitadas
 * enquanto a tela estiver aberta: o painel pode ser fechado e reaberto sem
 * nova chamada. Os três casos têm texto próprio: bucket não configurado,
 * SKU sem original, e as fotos. Um anúncio pode virar kit: cada grupo mostra
 * o próprio SKU.
 */
export function RawPhotosPanel({ listingId }: Props) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["raw-photos", listingId],
    queryFn: () => getRawPhotos(listingId),
    staleTime: Infinity,
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border bg-card p-4 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Procurando as fotos originais no bucket…
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        <span className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Não foi possível buscar as fotos originais agora
          {error instanceof Error && error.message ? `: ${error.message}` : "."}
        </span>
        <button type="button" onClick={() => refetch()} className="underline">
          Tentar de novo
        </button>
      </div>
    )
  }

  const view = rawPhotosView(data)

  if (view.kind === "unconfigured") {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        <p className="flex items-center gap-2 font-medium">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Bucket de fotos brutas não configurado para esta conta
        </p>
        <p className="mt-1">
          Sem o endereço do bucket, o sistema não sabe onde procurar as fotos originais. Informe-o em{" "}
          <Link href="/settings" className="underline">
            Configurações
          </Link>
          .
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-foreground">Fotos originais do produto</h2>
        <span className="text-xs text-slate-500">
          {view.total === 0 ? "nenhuma foto" : view.total === 1 ? "1 foto" : `${view.total} fotos`}
          {view.groups.length > 1 && ` · ${view.groups.length} SKUs`}
        </span>
      </div>

      {view.groups.length === 0 && (
        <p className="text-sm text-slate-500">Bucket configurado, mas este anúncio não tem SKU para procurar.</p>
      )}

      {view.groups.map((group) => (
        <div key={group.sku}>
          <p className="mb-2 text-xs font-medium text-slate-500">
            SKU <span className="font-mono text-foreground">{group.sku}</span>
            {group.empty && <span className="ml-2 font-normal">· bucket configurado, nenhum original deste SKU</span>}
          </p>
          {!group.empty && (
            <div className="grid grid-cols-3 gap-3 md:grid-cols-5 xl:grid-cols-6">
              {group.urls.map((url, i) => (
                <a
                  key={`${group.sku}-${i}`}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block overflow-hidden rounded-md border bg-slate-100"
                  title={`Abrir ${group.sku}-${i + 1} em nova aba`}
                >
                  {/* Foto do bucket público do seller, tamanho variável; next/image exigiria liberar o domínio. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt={`Original ${i + 1} do SKU ${group.sku}`} className="aspect-square w-full object-cover" loading="lazy" />
                </a>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
