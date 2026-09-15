"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { getListing } from "@/lib/api/listings"
import { useParams } from "next/navigation"
import { AttributeForm } from "@/components/listings/AttributeForm"
import { AttributeEditWarning } from "@/components/listings/AttributeEditWarning"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Loader2, ArrowLeft } from "lucide-react"
import Link from "next/link"
import type { AttributesEditResponse } from "@/types/listing"
import { isEditableStatus } from "@/lib/attribute-edit"

export default function AttributesPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const [aviso, setAviso] = useState<AttributesEditResponse | null>(null)

  const { data: listing, isLoading } = useQuery({
    queryKey: ["listing", id],
    queryFn: () => getListing(id),
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin text-slate-500" />
      </div>
    )
  }

  if (!listing) return null

  const mode = listing.status === "pending_seller_attributes" ? "submit" : "edit"

  if (!isEditableStatus(listing.status)) {
    return (
      <div className="max-w-xl mx-auto">
        <div className="mb-6">
          <Link
            href={`/listings/${id}`}
            className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-foreground mb-4"
          >
            <ArrowLeft className="w-4 h-4" />
            Voltar ao anúncio
          </Link>
        </div>
        <Card>
          <CardContent className="py-8 text-center text-sm text-slate-600">
            Este anúncio está em <span className="font-mono">{listing.status}</span> e não aceita
            correção de atributos agora.
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="max-w-xl mx-auto">
      <div className="mb-6">
        <Link
          href={`/listings/${id}`}
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-foreground mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          Voltar ao anúncio
        </Link>
        <h1 className="text-2xl font-bold text-foreground">
          {mode === "edit" ? "Corrigir atributos" : "Atributos do produto"}
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          {mode === "edit" ? (
            "Corrija um valor já gravado. Salvar não avança a etapa do anúncio."
          ) : (
            <>
              Preencha os atributos da categoria. Os marcados com{" "}
              <span className="text-red-500 font-medium">*</span> são obrigatórios pelo Mercado
              Livre.
            </>
          )}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {listing.selected_title || listing.sku_brand}
          </CardTitle>
          {listing.ml_category_id && (
            <p className="text-xs text-slate-400 font-mono">{listing.ml_category_id}</p>
          )}
        </CardHeader>
        <CardContent>
          <AttributeForm
            listingId={id}
            attributes={listing.attributes}
            mode={mode}
            onEdited={setAviso}
          />
        </CardContent>
      </Card>

      {aviso && <AttributeEditWarning listingId={id} result={aviso} />}
    </div>
  )
}
