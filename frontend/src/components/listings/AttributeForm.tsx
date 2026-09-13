"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { submitAttributes } from "@/lib/api/listings"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "sonner"
import { ChevronDown, Info, Loader2 } from "lucide-react"
import type { AttributeOut } from "@/types/listing"
import { classifyAttributes, fixedValue, isFixed } from "@/lib/attribute-visibility"

interface AttributeValue {
  value_id?: string
  value_name: string
}

interface Props {
  listingId: string
  attributes: AttributeOut[]
}

export function AttributeForm({ listingId, attributes }: Props) {
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

  const mutation = useMutation({
    mutationFn: () => {
      const payload = attributes
        .filter((attr) => values[attr.attribute_id]?.value_name?.trim())
        .map((attr) => ({
          attribute_id: attr.attribute_id,
          value_id: values[attr.attribute_id]?.value_id,
          value_name: values[attr.attribute_id]?.value_name ?? "",
        }))
      return submitAttributes(listingId, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["listing", listingId] })
      queryClient.invalidateQueries({ queryKey: ["listings"] })
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
        ) : (
          "Salvar e continuar"
        )}
      </Button>
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

      {isFixed(attr) ? (
        <Input
          id={attr.attribute_id}
          value={value?.value_name ?? ""}
          readOnly
          disabled
          aria-readonly="true"
        />
      ) : attr.allowed_values && attr.allowed_values.length > 0 ? (
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
          {attr.allowed_values.map((opt) => (
            <option key={opt.id} value={opt.id}>
              {opt.name}
            </option>
          ))}
        </select>
      ) : (
        <Input
          id={attr.attribute_id}
          value={value?.value_name ?? ""}
          onChange={(e) => onTextChange(attr.attribute_id, e.target.value)}
          placeholder={`Digite ${attr.attribute_name.toLowerCase()}`}
        />
      )}

      {isFixed(attr) ? (
        <p className="text-xs text-slate-400">Valor definido pela categoria; não pode ser alterado.</p>
      ) : (
        attr.source === "ai" && attr.value_name && (
          <p className="text-xs text-slate-400">Sugerido pela IA — confirme ou altere</p>
        )
      )}
    </div>
  )
}
