"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { submitAttributes, editAttributes } from "@/lib/api/listings"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "sonner"
import { ChevronDown, Info, Loader2 } from "lucide-react"
import type { AttributeOut, AttributesEditResponse, ListingSummary } from "@/types/listing"
import { classifyAttributes, fixedValue } from "@/lib/attribute-visibility"
import { fieldKindFor, matchSuggestion } from "@/lib/attribute-field"
import { buildEditPayload, isAttributesEditResponse } from "@/lib/attribute-edit"

interface AttributeValue {
  value_id?: string
  value_name: string
}

interface Props {
  listingId: string
  attributes: AttributeOut[]
  /** `edit` corrige atributo já gravado; `submit` (padrão) é o preenchimento inicial. */
  mode?: "submit" | "edit"
  /** Chamado no sucesso de `edit` com a resposta do PATCH (stale_positions/duplicated_fields). */
  onEdited?: (result: AttributesEditResponse) => void
}

export function AttributeForm({ listingId, attributes, mode = "submit", onEdited }: Props) {
  const router = useRouter()
  const queryClient = useQueryClient()

  // Só o que o operador deve preencher: `is_editable` vem do backend e
  // obrigatório aparece mesmo não editável (ver lib/attribute-visibility).
  // O envio continua filtrando por valor preenchido, então campo escondido
  // (vazio) simplesmente não entra no payload — nada é apagado.
  const { visible, hiddenCount, unclassified } = classifyAttributes(attributes)
  const required = visible.filter((a) => a.is_required)
  const optional = visible.filter((a) => !a.is_required)

  const initialValues: Record<string, AttributeValue> = {}
  attributes.forEach((attr) => {
    // Atributo `fixed` nasce com o único valor possível já no estado: o
    // backend não o pré-preenche, e sem isso o obrigatório nunca validaria.
    const fixed = fixedValue(attr)
    initialValues[attr.attribute_id] = fixed ?? {
      value_id: attr.value_id ?? undefined,
      value_name: attr.value_name ?? "",
    }
  })

  const [values, setValues] = useState<Record<string, AttributeValue>>(initialValues)
  const [optionalOpen, setOptionalOpen] = useState(false)

  const mutation = useMutation<ListingSummary | AttributesEditResponse, Error, void>({
    mutationFn: () => {
      if (mode === "edit") {
        // Em correção o payload leva o que foi ESVAZIADO. O filtro de valor
        // vazio do modo de preenchimento tornaria impossível limpar um campo.
        return editAttributes(listingId, buildEditPayload(attributes, values))
      }
      const payload = attributes
        .filter((attr) => values[attr.attribute_id]?.value_name?.trim())
        .map((attr) => ({
          attribute_id: attr.attribute_id,
          value_id: values[attr.attribute_id]?.value_id,
          value_name: values[attr.attribute_id]?.value_name ?? "",
        }))
      return submitAttributes(listingId, payload)
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
      if (mode === "edit") {
        toast.success("Correção salva. A etapa do anúncio não mudou.")
        // `mode` pode estar desatualizado em relação ao `result` (closure de
        // uma renderização mais nova que a que disparou a mutation — ver
        // `isAttributesEditResponse`). O caminho seguro quando o formato não
        // bate é simplesmente não chamar `onEdited`: sem isso o
        // `AttributeEditWarning` estouraria lendo `stale_positions` de um
        // `ListingSummary`. A correção em si já foi salva; só o aviso é
        // pulado.
        if (isAttributesEditResponse(result)) {
          onEdited?.(result)
        }
        return
      }
      toast.success("Atributos salvos com sucesso!")
      router.push(`/listings/${listingId}`)
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao salvar atributos")
    },
  })

  const handleSelectChange = (attrId: string, optId: string, optName: string) => {
    setValues((prev) => ({ ...prev, [attrId]: { value_id: optId, value_name: optName } }))
  }

  const handleTextChange = (attrId: string, text: string) => {
    setValues((prev) => ({ ...prev, [attrId]: { value_name: text } }))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const missing = required.filter((attr) => !values[attr.attribute_id]?.value_name?.trim())
    if (missing.length > 0) {
      toast.error(`Preencha os campos obrigatórios: ${missing.map((a) => a.attribute_name).join(", ")}`)
      return
    }
    mutation.mutate()
  }

  const notices = (
    <>
      {unclassified && (
        <p className="flex items-start gap-2 text-xs text-slate-500 bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-md px-3 py-2">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Os campos deste anúncio não foram classificados porque ele é anterior à classificação
            de atributos; por isso a lista está completa, incluindo campos internos do Mercado Livre.
          </span>
        </p>
      )}
      {hiddenCount > 0 && (
        <p className="text-xs text-slate-400">
          {hiddenCount} {hiddenCount === 1 ? "campo interno" : "campos internos"} do Mercado Livre{" "}
          {hiddenCount === 1 ? "oculto" : "ocultos"}.
        </p>
      )}
    </>
  )

  if (visible.length === 0) {
    return (
      <div className="text-center py-12 text-slate-500 space-y-4">
        <p>Nenhum atributo para preencher nesta categoria.</p>
        {notices}
        <Button className="mt-4" onClick={() => mutation.mutate()}>
          Continuar
        </Button>
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      {notices}
      {required.length > 0 && (
        <section className="space-y-4">
          <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">
            Obrigatórios
          </p>
          {required.map((attr) => (
            <AttributeField
              key={attr.attribute_id}
              attr={attr}
              value={values[attr.attribute_id]}
              onSelectChange={handleSelectChange}
              onTextChange={handleTextChange}
            />
          ))}
        </section>
      )}

      {optional.length > 0 && (
        <section>
          <button
            type="button"
            onClick={() => setOptionalOpen((o) => !o)}
            className="flex items-center justify-between w-full group"
          >
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest">
              Opcionais
              <span className="ml-2 text-slate-400 font-normal normal-case tracking-normal">
                ({optional.length})
              </span>
            </p>
            <ChevronDown
              className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${
                optionalOpen ? "rotate-180" : ""
              }`}
            />
          </button>

          <div
            className={`overflow-hidden transition-all duration-200 ${
              optionalOpen ? "max-h-[9999px] opacity-100 mt-4" : "max-h-0 opacity-0"
            }`}
          >
            <div className="space-y-4">
              {optional.map((attr) => (
                <AttributeField
                  key={attr.attribute_id}
                  attr={attr}
                  value={values[attr.attribute_id]}
                  onSelectChange={handleSelectChange}
                  onTextChange={handleTextChange}
                />
              ))}
            </div>
          </div>
        </section>
      )}

      <Button type="submit" disabled={mutation.isPending} className="w-full">
        {mutation.isPending ? (
          <>
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            Salvando...
          </>
        ) : mode === "edit" ? (
          "Salvar correção"
        ) : (
          "Salvar e continuar"
        )}
      </Button>
      {mode === "edit" && (
        <p className="text-xs text-slate-500 mt-2 text-center">
          Salvar não avança a etapa do anúncio.
        </p>
      )}
    </form>
  )
}

interface FieldProps {
  attr: AttributeOut
  value: AttributeValue | undefined
  onSelectChange: (id: string, optId: string, optName: string) => void
  onTextChange: (id: string, text: string) => void
}

function AttributeField({ attr, value, onSelectChange, onTextChange }: FieldProps) {
  // Quem decide o tipo de campo é o `attribute_type`, não a existência de
  // sugestões — ver `lib/attribute-field`.
  const kind = fieldKindFor(attr)
  const sugestoesId = `${attr.attribute_id}-sugestoes`
  const valorProprio =
    kind === "suggestions" &&
    !!value?.value_name?.trim() &&
    matchSuggestion(attr.allowed_values, value.value_name) === null

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Label htmlFor={attr.attribute_id} className="leading-none">
          {attr.attribute_name}
          {attr.is_required && <span className="text-red-500 ml-1">*</span>}
        </Label>
        <span className="text-[10px] font-mono text-slate-400 select-all">
          {attr.attribute_id}
        </span>
      </div>

      {kind === "fixed" ? (
        <Input
          id={attr.attribute_id}
          value={value?.value_name ?? ""}
          readOnly
          disabled
          aria-readonly="true"
        />
      ) : kind === "closed-list" ? (
        <select
          id={attr.attribute_id}
          value={value?.value_id ?? ""}
          onChange={(e) => {
            const selected = attr.allowed_values!.find((o) => o.id === e.target.value)
            if (selected) onSelectChange(attr.attribute_id, selected.id, selected.name)
          }}
          className="flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">Selecione...</option>
          {attr.allowed_values!.map((opt) => (
            <option key={opt.id} value={opt.id}>
              {opt.name}
            </option>
          ))}
        </select>
      ) : kind === "suggestions" ? (
        <>
          <Input
            id={attr.attribute_id}
            list={sugestoesId}
            value={value?.value_name ?? ""}
            onChange={(e) => {
              // Casou com uma sugestão: grava `value_id` + o nome EXATO do ML.
              // Valor novo: grava só o nome, sem id — é o que a documentação
              // do ML prescreve e o que o backend espera.
              const texto = e.target.value
              const casou = matchSuggestion(attr.allowed_values, texto)
              if (casou) onSelectChange(attr.attribute_id, casou.id, casou.name)
              else onTextChange(attr.attribute_id, texto)
            }}
            placeholder={`Digite ${attr.attribute_name.toLowerCase()} ou escolha uma sugestão`}
          />
          <datalist id={sugestoesId}>
            {attr.allowed_values!.map((opt) => (
              <option key={opt.id} value={opt.name} />
            ))}
          </datalist>
        </>
      ) : (
        <Input
          id={attr.attribute_id}
          value={value?.value_name ?? ""}
          onChange={(e) => onTextChange(attr.attribute_id, e.target.value)}
          placeholder={`Digite ${attr.attribute_name.toLowerCase()}`}
        />
      )}

      {kind === "fixed" ? (
        <p className="text-xs text-slate-400">Valor definido pela categoria; não pode ser alterado.</p>
      ) : (
        <>
          {kind === "suggestions" && (
            <p className="text-xs text-slate-400">
              {valorProprio ? (
                <>
                  Valor próprio, fora das {attr.allowed_values!.length} sugestões do Mercado Livre —
                  aceito nesta categoria.
                </>
              ) : (
                <>
                  {attr.allowed_values!.length}{" "}
                  {attr.allowed_values!.length === 1 ? "sugestão" : "sugestões"} do Mercado Livre;
                  digite outro valor se o seu não estiver na lista.
                </>
              )}
            </p>
          )}
          {attr.source === "ai" && attr.value_name && (
            <p className="text-xs text-slate-400">Sugerido pela IA — confirme ou altere</p>
          )}
        </>
      )}
    </div>
  )
}
