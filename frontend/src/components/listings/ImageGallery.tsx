"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { approveImages } from "@/lib/api/listings"
import { Button } from "@/components/ui/button"
import { toast } from "sonner"
import { Loader2, CheckSquare, Square } from "lucide-react"
import type { ImageOut } from "@/types/listing"

interface Props {
  listingId: string
  images: ImageOut[]
}

function getImageUrl(image: ImageOut): string | null {
  if (image.ml_picture_id) {
    return `https://http2.mlstatic.com/D_NQ_NP_${image.ml_picture_id}-V.jpg`
  }
  return null
}

export function ImageGallery({ listingId, images }: Props) {
  const router = useRouter()
  const queryClient = useQueryClient()

  const [approved, setApproved] = useState<Set<string>>(
    new Set(images.filter((i) => i.approved).map((i) => i.id))
  )

  const mutation = useMutation({
    mutationFn: () => approveImages(listingId, Array.from(approved)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      toast.success("Imagens aprovadas com sucesso!")
      router.push(`/listings/${listingId}`)
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao aprovar imagens")
    },
  })

  const toggleApproval = (id: string) => {
    setApproved((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleSubmit = () => {
    if (approved.size === 0) {
      toast.error("Selecione pelo menos uma imagem para aprovar.")
      return
    }
    mutation.mutate()
  }

  if (images.length === 0) {
    return (
      <div className="text-center py-12 text-slate-500">
        <p>Nenhuma imagem gerada ainda.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-600">
        Selecione as imagens que deseja usar no anúncio. Pelo menos uma imagem deve ser aprovada.
        {approved.size > 0 && (
          <span className="ml-2 font-medium text-blue-600">
            {approved.size} selecionada{approved.size !== 1 ? "s" : ""}
          </span>
        )}
      </p>

      <div className="grid grid-cols-2 gap-4">
        {images.map((image) => {
          const url = getImageUrl(image)
          const isApproved = approved.has(image.id)

          return (
            <div
              key={image.id}
              onClick={() => toggleApproval(image.id)}
              className={`relative cursor-pointer rounded-lg overflow-hidden border-2 transition-all ${
                isApproved
                  ? "border-blue-500 shadow-md"
                  : "border-slate-200 hover:border-slate-300"
              }`}
            >
              <div className="aspect-square bg-slate-100 relative">
                {url ? (
                  <img
                    src={url}
                    alt={`Imagem ${image.sort_order + 1}`}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-slate-400 text-sm">
                    Sem prévia
                  </div>
                )}

                <div className="absolute top-2 right-2">
                  {isApproved ? (
                    <CheckSquare className="w-6 h-6 text-blue-500 bg-white rounded" />
                  ) : (
                    <Square className="w-6 h-6 text-slate-400 bg-white rounded" />
                  )}
                </div>

                {isApproved && (
                  <div className="absolute inset-0 bg-blue-500/10 pointer-events-none" />
                )}
              </div>

              <div className="p-2 text-center">
                <span className="text-xs text-slate-500">
                  Imagem {image.sort_order + 1}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      <Button
        onClick={handleSubmit}
        disabled={mutation.isPending || approved.size === 0}
        className="w-full"
      >
        {mutation.isPending ? (
          <>
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            Aprovando...
          </>
        ) : (
          `Aprovar ${approved.size} ${approved.size !== 1 ? "imagens" : "imagem"}`
        )}
      </Button>
    </div>
  )
}
