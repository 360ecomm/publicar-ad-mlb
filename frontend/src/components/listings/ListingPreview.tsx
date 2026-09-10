"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { publishListing } from "@/lib/api/listings"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { toast } from "sonner"
import { Loader2, ChevronLeft, ChevronRight } from "lucide-react"
import type { ListingDetail, ImageOut } from "@/types/listing"
import { formatPrice, formatQuantity } from "@/lib/utils"

function getImageUrl(image: ImageOut): string | null {
  if (image.ml_picture_id) {
    return `https://http2.mlstatic.com/D_NQ_NP_${image.ml_picture_id}-V.jpg`
  }
  return null
}

interface Props {
  listing: ListingDetail
}

export function ListingPreview({ listing }: Props) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [currentImage, setCurrentImage] = useState(0)

  const approvedImages = listing.images.filter((i) => i.approved)

  const mutation = useMutation({
    mutationFn: () => publishListing(listing.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", listing.id] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Anúncio publicado com sucesso!")
      router.push(`/listings/${listing.id}`)
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao publicar anúncio")
    },
  })

  const filledAttributes = listing.attributes.filter((a) => a.value_name)

  return (
    <div className="space-y-6">
      {/* Image carousel */}
      {approvedImages.length > 0 && (
        <div className="relative">
          <div className="aspect-square bg-muted rounded-lg overflow-hidden">
            {(() => {
              const url = getImageUrl(approvedImages[currentImage])
              return url ? (
                <img
                  src={url}
                  alt={`Imagem ${currentImage + 1}`}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-slate-400">
                  Sem prévia
                </div>
              )
            })()}
          </div>

          {approvedImages.length > 1 && (
            <>
              <button
                onClick={() =>
                  setCurrentImage((prev) => (prev - 1 + approvedImages.length) % approvedImages.length)
                }
                className="absolute left-2 top-1/2 -translate-y-1/2 bg-background/80 hover:bg-background rounded-full p-1 shadow"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>
              <button
                onClick={() =>
                  setCurrentImage((prev) => (prev + 1) % approvedImages.length)
                }
                className="absolute right-2 top-1/2 -translate-y-1/2 bg-background/80 hover:bg-background rounded-full p-1 shadow"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
              <div className="flex justify-center gap-1.5 mt-2">
                {approvedImages.map((_, i) => (
                  <button
                    key={i}
                    onClick={() => setCurrentImage(i)}
                    className={`w-2 h-2 rounded-full transition-colors ${
                      i === currentImage ? "bg-blue-500" : "bg-slate-300"
                    }`}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Title */}
      <div>
        <h2 className="text-lg font-semibold text-foreground leading-snug">
          {listing.selected_title || "Título não definido"}
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          Marca: <span className="font-medium">{listing.sku_brand}</span>
        </p>
      </div>

      {/* Price */}
      <Card>
        <CardContent className="p-4">
          <p className="text-2xl font-bold text-foreground">
            R$ {formatPrice(listing.price)}
          </p>
          <p className="text-sm text-slate-500 mt-1">
            {formatQuantity(listing.stock_quantity)} em estoque ·{" "}
            {listing.condition === "new" ? "Novo" : "Usado"}
          </p>
        </CardContent>
      </Card>

      {/* Description */}
      {listing.description_html && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground mb-2">Descrição</h3>
          <div
            className="text-sm text-slate-600 prose prose-sm max-w-none border rounded-lg p-3"
            dangerouslySetInnerHTML={{ __html: listing.description_html }}
          />
        </div>
      )}

      {/* Attributes */}
      {filledAttributes.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground mb-2">Atributos</h3>
          <div className="space-y-1">
            {filledAttributes.map((attr) => (
              <div key={attr.id} className="flex justify-between text-sm py-1 border-b border-border">
                <span className="text-slate-500">{attr.attribute_name}</span>
                <span className="font-medium text-foreground">{attr.value_name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Publish button */}
      <Button
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="w-full"
        size="lg"
      >
        {mutation.isPending ? (
          <>
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            Publicando...
          </>
        ) : (
          "Confirmar e Publicar"
        )}
      </Button>

      <p className="text-xs text-slate-400 text-center">
        O anúncio será publicado diretamente no Mercado Livre.
      </p>
    </div>
  )
}
