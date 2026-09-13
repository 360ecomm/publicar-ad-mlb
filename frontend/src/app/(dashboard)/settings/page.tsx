"use client"

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { listSellers } from "@/lib/api/sellers"
import { getMLConnectUrl } from "@/lib/api/auth"
import { getTitleConfigs, createTitleConfig, updateTitleConfig, deleteTitleConfig } from "@/lib/api/title-configs"
import { getSellerImageConfig, upsertSellerImageConfig } from "@/lib/api/seller-image-config"
import { useSeller } from "@/contexts/SellerContext"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Loader2, CheckCircle, ExternalLink, ShoppingBag, Plus, Check, Tag, Pencil, Trash2, ImageIcon } from "lucide-react"
import { toast } from "sonner"
import { useState } from "react"
import { format } from "date-fns"
import { ptBR } from "date-fns/locale"
import type { TitleConfig, TitleConfigCreate, TitleConfigUpdate } from "@/types/title-config"

const AVAILABLE_TOKENS = [
  "{descricao_erp}",
  "{marca}",
  "{modelo}",
  "{referencia_tecnica}",
  "{aplicacao_veiculo}",
  "{cor}",
  "{tamanho}",
  "{capacidade}",
  "{material}",
  "{genero}",
  "{condicao}",
  "{ean}",
  "{seo}",
]

const EMPTY_FORM = {
  product_group: "",
  title_structure: "",
  title_rules: "",
  is_default: false,
}

export default function SettingsPage() {
  const [connecting, setConnecting] = useState(false)
  const { activeSeller, setActiveSeller } = useSeller()

  // --- Title Config form state ---
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [formData, setFormData] = useState(EMPTY_FORM)

  const queryClient = useQueryClient()

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["sellers"],
    queryFn: listSellers,
    retry: 1,
    refetchOnWindowFocus: true,
  })

  const { data: titleConfigs = [], isLoading: isLoadingConfigs } = useQuery({
    queryKey: ["title-configs"],
    queryFn: getTitleConfigs,
    retry: 1,
  })

  const createMutation = useMutation({
    mutationFn: (payload: TitleConfigCreate) => createTitleConfig(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["title-configs"] })
      toast.success("Configuração criada com sucesso.")
      resetForm()
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao criar configuração.")
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TitleConfigUpdate }) =>
      updateTitleConfig(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["title-configs"] })
      toast.success("Configuração atualizada.")
      resetForm()
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao atualizar configuração.")
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTitleConfig(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["title-configs"] })
      toast.success("Configuração removida.")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao remover configuração.")
    },
  })

  // --- Seller Image Config form state ---
  const [imageConfigForm, setImageConfigForm] = useState({
    raw_base_url: "",
  })

  const { data: imageConfig } = useQuery({
    queryKey: ["seller-image-config"],
    queryFn: getSellerImageConfig,
    retry: 1,
  })

  const imageConfigMutation = useMutation({
    mutationFn: upsertSellerImageConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["seller-image-config"] })
      toast.success("Configuração de imagens salva.")
    },
    onError: (err: Error) => {
      toast.error(err.message || "Erro ao salvar configuração de imagens.")
    },
  })

  function handleImageConfigSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!imageConfigForm.raw_base_url.trim()) {
      toast.error("URL base de leitura é obrigatória.")
      return
    }
    imageConfigMutation.mutate({
      raw_base_url: imageConfigForm.raw_base_url.trim(),
    })
  }

  function resetForm() {
    setFormData(EMPTY_FORM)
    setEditingId(null)
    setShowForm(false)
  }

  function handleEdit(config: TitleConfig) {
    setEditingId(config.id)
    setFormData({
      product_group: config.product_group,
      title_structure: config.title_structure,
      title_rules: config.title_rules ?? "",
      is_default: config.is_default,
    })
    setShowForm(true)
  }

  function handleDelete(config: TitleConfig) {
    if (!window.confirm(`Remover configuração para "${config.product_group}"?`)) return
    deleteMutation.mutate(config.id)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!formData.product_group.trim() || !formData.title_structure.trim()) {
      toast.error("Grupo de produto e estrutura do título são obrigatórios.")
      return
    }
    if (editingId) {
      updateMutation.mutate({
        id: editingId,
        payload: {
          title_structure: formData.title_structure.trim(),
          title_rules: formData.title_rules.trim() || null,
          is_default: formData.is_default,
        },
      })
    } else {
      createMutation.mutate({
        product_group: formData.product_group.trim(),
        title_structure: formData.title_structure.trim(),
        title_rules: formData.title_rules.trim() || null,
        is_default: formData.is_default,
      })
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending

  const handleConnect = async () => {
    setConnecting(true)
    try {
      const url = await getMLConnectUrl()
      // Aba nova: o operador não perde a aplicação. O callback devolve a aba
      // nova em /contas?ml_connected=true; a lista daqui refaz no foco
      // (refetchOnWindowFocus já está ligado na query "sellers").
      window.open(url, "_blank", "noopener")
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro ao obter URL de conexão"
      toast.error(message)
    } finally {
      setConnecting(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Configurações</h1>
        <p className="text-sm text-slate-500 mt-1">
          Gerencie as contas do Mercado Livre conectadas e as estruturas de título por grupo de produto.
        </p>
      </div>

      {/* ── Contas ML ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShoppingBag className="w-5 h-5 text-yellow-500" />
                Contas Mercado Livre
              </CardTitle>
              <CardDescription className="mt-1">
                Cada conta conectada pode ter seus próprios anúncios gerenciados independentemente.
              </CardDescription>
            </div>
            <Button size="sm" onClick={handleConnect} disabled={connecting}>
              {connecting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <Plus className="w-4 h-4 mr-1" />
                  Conectar conta
                </>
              )}
            </Button>
          </div>
        </CardHeader>

        <CardContent>
          {isLoading ? (
            <div className="flex items-center gap-2 text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span className="text-sm">Carregando contas...</span>
            </div>
          ) : sellers.length === 0 ? (
            <div className="text-center py-8">
              <ShoppingBag className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <p className="text-slate-500 text-sm">Nenhuma conta conectada ainda.</p>
              <Button className="mt-4" onClick={handleConnect} disabled={connecting}>
                {connecting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Redirecionando...
                  </>
                ) : (
                  <>
                    <ExternalLink className="w-4 h-4 mr-2" />
                    Conectar conta Mercado Livre
                  </>
                )}
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              {sellers.map((seller) => {
                const isActive = activeSeller?.id === seller.id
                return (
                  <div
                    key={seller.id}
                    className={`border rounded-lg p-4 transition-colors ${
                      isActive ? "border-green-300 bg-green-50 dark:bg-green-950/30" : "border-border bg-card"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        <CheckCircle className={`w-4 h-4 flex-shrink-0 ${isActive ? "text-green-600" : "text-slate-400"}`} />
                        <span className="font-medium text-foreground truncate">{seller.ml_nickname}</span>
                        {isActive && (
                          <span className="text-xs font-medium text-green-700 bg-green-100 px-2 py-0.5 rounded-full flex-shrink-0">
                            Ativa
                          </span>
                        )}
                        {!seller.is_active && (
                          <span className="text-xs font-medium text-slate-600 bg-slate-100 px-2 py-0.5 rounded-full flex-shrink-0">
                            Desconectada
                          </span>
                        )}
                      </div>
                      {seller.is_active && !isActive && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setActiveSeller(seller)}
                          className="flex-shrink-0"
                        >
                          <Check className="w-3.5 h-3.5 mr-1" />
                          Usar esta
                        </Button>
                      )}
                    </div>

                    <div className="mt-3 space-y-1 text-sm text-slate-500">
                      <div className="flex justify-between">
                        <span>ML User ID</span>
                        <span className="font-mono text-xs">{seller.ml_user_id}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Token expira em</span>
                        <span>
                          {format(new Date(seller.token_expires_at), "dd/MM/yyyy HH:mm", { locale: ptBR })}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>Conectada em</span>
                        <span>
                          {format(new Date(seller.granted_at), "dd/MM/yyyy", { locale: ptBR })}
                        </span>
                      </div>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-3 text-slate-500 hover:text-foreground w-full justify-start px-0"
                      onClick={handleConnect}
                      disabled={connecting}
                    >
                      <ExternalLink className="w-3.5 h-3.5 mr-1.5" />
                      Reconectar / renovar token
                    </Button>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Estrutura de títulos ───────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Tag className="w-5 h-5 text-blue-500" />
                Estrutura de títulos
              </CardTitle>
              <CardDescription className="mt-1">
                Defina templates de título e regras de geração por grupo de produto.
              </CardDescription>
            </div>
            {!showForm && (
              <Button
                size="sm"
                onClick={() => {
                  setFormData(EMPTY_FORM)
                  setEditingId(null)
                  setShowForm(true)
                }}
              >
                <Plus className="w-4 h-4 mr-1" />
                Adicionar configuração
              </Button>
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          {/* Lista de configurações existentes */}
          {isLoadingConfigs ? (
            <div className="flex items-center gap-2 text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span className="text-sm">Carregando configurações...</span>
            </div>
          ) : titleConfigs.length === 0 && !showForm ? (
            <div className="text-center py-8">
              <Tag className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <p className="text-slate-500 text-sm">Nenhuma configuração de título definida.</p>
              <p className="text-slate-400 text-xs mt-1">
                Sem configuração, a IA usa uma estrutura padrão genérica.
              </p>
            </div>
          ) : titleConfigs.length > 0 ? (
            <div className="rounded-md border overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 dark:bg-slate-800 border-b">
                    <th className="text-left px-3 py-2 font-medium text-slate-600 dark:text-slate-300">
                      Grupo do produto
                    </th>
                    <th className="text-left px-3 py-2 font-medium text-slate-600 dark:text-slate-300">
                      Estrutura do título
                    </th>
                    <th className="text-center px-3 py-2 font-medium text-slate-600 dark:text-slate-300 w-20">
                      Padrão
                    </th>
                    <th className="px-3 py-2 w-24" />
                  </tr>
                </thead>
                <tbody>
                  {titleConfigs.map((config) => (
                    <tr
                      key={config.id}
                      className="border-b last:border-0 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors"
                    >
                      <td className="px-3 py-2.5 font-medium text-foreground">
                        {config.product_group}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs text-slate-600 dark:text-slate-400 max-w-[220px] truncate">
                        {config.title_structure}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        {config.is_default ? (
                          <span className="inline-block text-xs font-medium text-blue-700 bg-blue-100 dark:bg-blue-900/40 dark:text-blue-300 px-2 py-0.5 rounded-full">
                            Sim
                          </span>
                        ) : (
                          <span className="text-slate-300 dark:text-slate-600">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 text-slate-500 hover:text-foreground"
                            onClick={() => handleEdit(config)}
                            title="Editar"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 p-0 text-slate-500 hover:text-red-600"
                            onClick={() => handleDelete(config)}
                            title="Excluir"
                            disabled={deleteMutation.isPending}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {/* Formulário inline */}
          {showForm && (
            <form
              onSubmit={handleSubmit}
              className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/20 p-4 space-y-4"
            >
              <p className="text-sm font-semibold text-foreground">
                {editingId ? "Editar configuração" : "Nova configuração"}
              </p>

              <div className="grid grid-cols-2 gap-4">
                {/* Grupo de produto */}
                <div className="space-y-1.5">
                  <Label htmlFor="tc-group">
                    Grupo de produto <span className="text-red-500">*</span>
                  </Label>
                  <Input
                    id="tc-group"
                    placeholder="ex: rolamentos"
                    value={formData.product_group}
                    onChange={(e) => setFormData((f) => ({ ...f, product_group: e.target.value }))}
                    disabled={!!editingId}
                    required
                  />
                </div>

                {/* É padrão */}
                <div className="space-y-1.5 flex flex-col justify-end">
                  <Label htmlFor="tc-default" className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      id="tc-default"
                      type="checkbox"
                      className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                      checked={formData.is_default}
                      onChange={(e) => setFormData((f) => ({ ...f, is_default: e.target.checked }))}
                    />
                    <span>Usar como padrão</span>
                  </Label>
                </div>
              </div>

              {/* Estrutura do título */}
              <div className="space-y-1.5">
                <Label htmlFor="tc-structure">
                  Estrutura do título <span className="text-red-500">*</span>
                </Label>
                <Input
                  id="tc-structure"
                  placeholder="ex: {referencia_tecnica} {marca}"
                  value={formData.title_structure}
                  onChange={(e) => setFormData((f) => ({ ...f, title_structure: e.target.value }))}
                  required
                />
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {AVAILABLE_TOKENS.map((token) => (
                    <button
                      key={token}
                      type="button"
                      onClick={() =>
                        setFormData((f) => ({
                          ...f,
                          title_structure: f.title_structure
                            ? `${f.title_structure} ${token}`
                            : token,
                        }))
                      }
                      className="text-xs font-mono px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-blue-100 dark:hover:bg-blue-900/40 hover:text-blue-700 dark:hover:text-blue-300 transition-colors border border-slate-200 dark:border-slate-700"
                      title={`Inserir ${token}`}
                    >
                      {token}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-slate-400">
                  Clique nos tokens para inserir na estrutura.
                </p>
              </div>

              {/* Regras adicionais */}
              <div className="space-y-1.5">
                <Label htmlFor="tc-rules">Regras adicionais</Label>
                <textarea
                  id="tc-rules"
                  rows={3}
                  placeholder={`Exemplo: "Códigos técnicos (DDU, C3, 2RS) são intocáveis — jamais abreviar. Se {aplicacao_veiculo} estiver preenchido, incluir ao final."`}
                  value={formData.title_rules}
                  onChange={(e) => setFormData((f) => ({ ...f, title_rules: e.target.value }))}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
                />
                <p className="text-xs text-slate-400">
                  Instruções específicas para a IA ao gerar títulos neste grupo. Opcional.
                </p>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <Button type="submit" size="sm" disabled={isSaving}>
                  {isSaving ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                      Salvando...
                    </>
                  ) : (
                    "Salvar"
                  )}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={resetForm}
                  disabled={isSaving}
                >
                  Cancelar
                </Button>
              </div>
            </form>
          )}

          {/* Botão adicionar (quando lista existe mas form não aberto) */}
          {!showForm && titleConfigs.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => {
                setFormData(EMPTY_FORM)
                setEditingId(null)
                setShowForm(true)
              }}
            >
              <Plus className="w-4 h-4 mr-1" />
              Adicionar configuração
            </Button>
          )}
        </CardContent>
      </Card>

      {/* ── Configuração de imagens ──────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ImageIcon className="w-5 h-5 text-purple-500" />
            Configuração de imagens
          </CardTitle>
          <CardDescription className="mt-1">
            Bucket próprio com as fotos brutas do produto (2 por SKU: <code>SKU-1.jpg</code>, <code>SKU-2.jpg</code>).
            Sem essa configuração, o pipeline continua gerando imagens por IA a partir do texto, como hoje.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleImageConfigSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="ic-raw-url">
                URL base de leitura <span className="text-red-500">*</span>
              </Label>
              <Input
                id="ic-raw-url"
                placeholder="ex: https://pub-xxx.r2.dev/sku"
                defaultValue={imageConfig?.raw_base_url ?? ""}
                onChange={(e) => setImageConfigForm((f) => ({ ...f, raw_base_url: e.target.value }))}
              />
            </div>

            <Button type="submit" size="sm" disabled={imageConfigMutation.isPending}>
              {imageConfigMutation.isPending ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  Salvando...
                </>
              ) : (
                "Salvar configuração"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
