"use client"

import { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { listSellers, type SellerOut } from "@/lib/api/sellers"

interface SellerContextValue {
  /** Todas as contas que o usuário acessa, inclusive desconectadas (para /contas). */
  sellers: SellerOut[]
  /** Só as ativas: as únicas que podem ser a conta ativa (o backend recusa X-Seller-ID inativo com 403). */
  connectedSellers: SellerOut[]
  activeSeller: SellerOut | null
  setActiveSeller: (seller: SellerOut) => void
  isLoading: boolean
  reload: () => Promise<void>
}

const SellerContext = createContext<SellerContextValue | null>(null)

const STORAGE_KEY = "active_seller_id"

function persist(id: string | null) {
  if (typeof window === "undefined") return
  if (id) localStorage.setItem(STORAGE_KEY, id)
  else localStorage.removeItem(STORAGE_KEY)
}

export function SellerProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const [sellers, setSellers] = useState<SellerOut[]>([])
  const [activeSeller, setActiveSellerState] = useState<SellerOut | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const connectedSellers = useMemo(() => sellers.filter((s) => s.is_active), [sellers])

  // Última conta ativa vista por `load`, para saber se a troca exige reset
  // de cache. `primedRef` marca se `load` já rodou ao menos uma vez: a
  // PRIMEIRA resolução (antes de qualquer chamada, não há "conta anterior")
  // não pode disparar reset de um cache que ainda está vazio — só a partir
  // da segunda é que existe de fato uma troca.
  const lastActiveIdRef = useRef<string | null>(null)
  const primedRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const data = await listSellers()
      setSellers(data)

      // Só uma conta ATIVA pode ser a ativa: `get_active_seller` recusa
      // X-Seller-ID de conta desconectada com 403. Antes o contexto pegava
      // data[0] sem olhar is_active — com uma desconectada em primeiro
      // lugar, toda chamada falhava.
      const connected = data.filter((s) => s.is_active)
      const savedId = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null
      const found = savedId ? connected.find((s) => s.id === savedId) : null
      const active = found ?? connected[0] ?? null
      // localStorage ANTES do setState: o re-render dispara queries e o
      // apiFetch lê o X-Seller-ID de lá.
      persist(active?.id ?? null)
      setActiveSellerState(active)

      // `reload` pode trocar a conta ativa por baixo do tapete (a conta
      // anterior foi desconectada, ou outra aba gravou outro id no
      // localStorage compartilhado): a tela não pode ficar com dados da
      // conta anterior no cache. Mesmo invariante de `setActiveSeller`.
      const nextId = active?.id ?? null
      if (primedRef.current && lastActiveIdRef.current !== nextId) {
        void queryClient.resetQueries()
      }
      primedRef.current = true
      lastActiveIdRef.current = nextId
    } catch {
      // Sem token o layout já redireciona para /login; não propaga.
    } finally {
      setIsLoading(false)
    }
  }, [queryClient])

  useEffect(() => {
    load()
  }, [load])

  // localStorage é compartilhado entre abas e o `apiFetch` lê o
  // X-Seller-ID de lá a cada chamada. Sem isto, a aba B continua mostrando
  // a conta X enquanto manda o header da conta Y que a aba A acabou de
  // escolher — escrita na conta errada (ex.: `PUT /sellers/image-config`
  // não leva id no corpo, só o header). `e.key === null` cobre o
  // `localStorage.clear()`.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY || e.key === null) {
        void load()
      }
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [load])

  const setActiveSeller = useCallback(
    (seller: SellerOut) => {
      if (!seller.is_active) return
      if (activeSeller?.id === seller.id) return
      // Ordem importa: storage primeiro (o refetch lê o header de lá), depois
      // o estado, depois o reset.
      persist(seller.id)
      setActiveSellerState(seller)
      lastActiveIdRef.current = seller.id
      // `resetQueries` SEM filtro, de propósito: é o reset sem filtro que
      // limpa a chave ANTIGA antes do re-render, e é isso que impede o
      // `placeholderData: (prev) => prev` do WorkQueue de pintar as linhas
      // da conta anterior — e a chave antiga de continuar guardando a
      // resposta de outra conta. Um "ajuste" para `resetQueries({ queryKey })`
      // reintroduziria exatamente o defeito que esta branch fecha.
      void queryClient.resetQueries()
    },
    [activeSeller, queryClient]
  )

  return (
    <SellerContext.Provider
      value={{ sellers, connectedSellers, activeSeller, setActiveSeller, isLoading, reload: load }}
    >
      {children}
    </SellerContext.Provider>
  )
}

export function useSeller(): SellerContextValue {
  const ctx = useContext(SellerContext)
  if (!ctx) throw new Error("useSeller deve ser usado dentro de <SellerProvider>")
  return ctx
}
