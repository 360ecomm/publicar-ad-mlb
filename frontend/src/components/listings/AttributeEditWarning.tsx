"use client"

import Link from "next/link"
import { AlertTriangle } from "lucide-react"
import type { AttributesEditResponse } from "@/types/listing"

const NOME_DA_POSICAO: Record<number, string> = {
  0: "capa",
  1: "apresentação",
  2: "benefícios",
  3: "detalhe",
  4: "ficha técnica",
}

/**
 * Aviso depois de uma correção: o que ficou divergente entre banco e imagem.
 *
 * Só avisa — regenerar por conta própria gastaria chamada paga sem decisão
 * humana. O botão de regenerar posição mora na tela de revisão de imagens,
 * que ainda não existe (bloco B); até lá o caminho é o link da galeria.
 */
export function AttributeEditWarning({
  listingId,
  result,
}: {
  listingId: string
  result: AttributesEditResponse
}) {
  const { stale_positions: stale, duplicated_fields: dup } = result
  if (stale.length === 0 && dup.length === 0) return null

  return (
    <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
        <div className="text-sm text-amber-900 space-y-3">
          {stale.length > 0 && (
            <div>
              <p className="font-medium">
                Esta correção mudou texto impresso {stale.length === 1 ? "na imagem" : "nas imagens"}{" "}
                {stale.map((p) => `${p} (${NOME_DA_POSICAO[p] ?? "posição"})`).join(", ")}.
              </p>
              <p className="mt-1">
                A imagem é o que o comprador vê. Regere{" "}
                {stale.length === 1 ? "essa posição" : "essas posições"} antes de publicar.
              </p>
              <Link
                href={`/listings/${listingId}/images`}
                className="inline-block mt-2 underline underline-offset-2 font-medium"
              >
                Abrir as imagens do anúncio
              </Link>
            </div>
          )}
          {dup.length > 0 && (
            <p>
              {dup.join(" e ")} {dup.length === 1 ? "existe" : "existem"} em duplicata: esta
              correção muda a ficha técnica, mas <strong>não</strong> muda a imagem de
              apresentação, que lê o valor do catálogo de produtos. Corrija também no
              catálogo se o valor estiver errado lá.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
