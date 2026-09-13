// frontend/src/app/(dashboard)/listings/attributes/page.tsx
"use client"

import { useQuery } from "@tanstack/react-query"
import { getListingsForGrid } from "@/lib/api/listings"
import { AttributeGridEditor } from "@/components/listings/AttributeGridEditor"
import { Loader2 } from "lucide-react"
import Link from "next/link"

export default function AttributesGridPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["listings-for-grid"],
    queryFn: getListingsForGrid,
  })

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/listings" className="text-sm text-slate-500 hover:text-slate-700">← Voltar à fila</Link>
        <h1 className="text-xl font-semibold text-foreground">Atributos por anúncio</h1>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-slate-500">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span className="text-sm">Carregando...</span>
        </div>
      )}

      {error && (
        <p className="text-sm text-red-600">Erro ao carregar anúncios. Tente recarregar.</p>
      )}

      {data && <AttributeGridEditor rows={data} />}
    </div>
  )
}
