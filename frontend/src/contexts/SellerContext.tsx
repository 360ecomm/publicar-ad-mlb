"use client"

import { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { listSellers, type SellerOut } from "@/lib/api/sellers"

interface SellerContextValue {
  /** Todas as contas que o usuário acessa, inclusive desconectadas (para /contas). */
  sellers: SellerOut[]
  /** Só as ativas: as únicas que podem ser a conta ativa (o backend recusa X-Seller-ID inativo com 403). */
  connectedSellers: SellerOut[]
  activeSeller: SellerOut | null
  setActiveSeller: (seller: SellerOut) => void
  clearActiveSeller: () => void
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
    } catch {
      // Sem token o layout já redireciona para /login; não propaga.
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const setActiveSeller = useCallback(
    (seller: SellerOut) => {
      if (!seller.is_active) return
      if (activeSeller?.id === seller.id) return
      // Ordem importa: storage primeiro (o refetch lê o header de lá), depois
      // o estado, depois o reset. `resetQueries` (não `invalidateQueries`)
      // apaga o cache e refaz as ativas: a tela nunca mostra dados da conta
      // anterior enquanto os novos chegam — trocar de conta e agir sobre o
      // anúncio errado seria um erro invisível.
      persist(seller.id)
      setActiveSellerState(seller)
      void queryClient.resetQueries()
    },
    [activeSeller, queryClient]
  )

  const clearActiveSeller = useCallback(() => {
    persist(null)
    setActiveSellerState(null)
    void queryClient.resetQueries()
  }, [queryClient])

  return (
    <SellerContext.Provider
      value={{ sellers, connectedSellers, activeSeller, setActiveSeller, clearActiveSeller, isLoading, reload: load }}
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
