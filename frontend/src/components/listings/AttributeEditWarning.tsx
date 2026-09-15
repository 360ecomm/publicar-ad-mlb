"use client"

import Link from "next/link"
import { AlertTriangle } from "lucide-react"
import type { AttributesEditResponse } from "@/types/listing"
import { POSITION_LABELS, isGalleryPosition } from "@/lib/image-review"

/**
 * Nome e número da posição na convenção da TELA, não na da API: a API conta
 * de 0 e a tela de 1 (ver `describeRegenerateError` em lib/image-review).
 * Um número que não bate com o da galeria manda o operador para a posição
 * errada. Os rótulos são os de `POSITION_LABELS` — uma definição só; o
 * dicionário próprio que existia aqui já divergia ("detalhe" × "Detalhes").
 */
function rotuloDaPosicao(p: number): string {
  return isGalleryPosition(p) ? `${p + 1} (${POSITION_LABELS[p]})` : `${p + 1}`
}

/**
 * Aviso depois de uma correção: o que ficou divergente entre banco e imagem.
 *
 * Só avisa — regenerar por conta própria gastaria chamada paga sem decisão
 * humana. Quem regenera é o operador, pelo botão "Regenerar" de cada posição
 * na tela de revisão de imagens (`/listings/[id]/images`), que é para onde o
 * link abaixo leva. Em `ready_to_publish` esse botão aparece também nas
 * posições já aprovadas: regenerar dali desfaz a aprovação daquela posição e
 * devolve o anúncio à revisão (ver `canRegenerate`).
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
                {stale.map(rotuloDaPosicao).join(", ")}.
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
