"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ShoppingBag } from "lucide-react"
import { useSeller } from "@/contexts/SellerContext"

/**
 * Barra fixa no topo do conteúdo: a conta ativa, sempre visível.
 *
 * Fica no conteúdo, não no menu lateral, porque o menu abre recolhido
 * (só ícones) — o seletor que vivia no rodapé dele sumia com o nome da
 * conta. Aqui não depende do menu estar aberto e ainda diz em que conta
 * o operador está agindo, em qualquer tela.
 */
export function AccountBar() {
  const { connectedSellers, activeSeller, setActiveSeller, isLoading } = useSeller()
  // A barra aparece em toda tela do painel, inclusive na própria /contas. Lá,
  // os dois atalhos daqui apontariam para a página aberta: link que não leva
  // a lugar nenhum é ruído, e o operador clica achando que vai acontecer algo.
  const naPaginaDeContas = usePathname() === "/contas"

  return (
    <div
      data-testid="account-bar"
      className="h-12 flex-shrink-0 border-b border-border bg-background/95 backdrop-blur px-6 flex items-center gap-3 text-sm"
    >
      <ShoppingBag className={`w-4 h-4 flex-shrink-0 ${activeSeller ? "text-yellow-500" : "text-slate-400"}`} />
      <span className="text-slate-500">Conta ativa:</span>

      {isLoading ? (
        <span className="text-slate-400">carregando…</span>
      ) : !activeSeller ? (
        <span className="text-amber-600">
          Nenhuma conta conectada.
          {!naPaginaDeContas && (
            <>
              {" "}
              <Link href="/contas" className="underline hover:text-amber-700">Conectar conta</Link>
            </>
          )}
        </span>
      ) : connectedSellers.length === 1 ? (
        // Uma conta só: mostra, sem menu de troca (não há para onde trocar).
        <span className="font-medium text-foreground">{activeSeller.ml_nickname}</span>
      ) : (
        <select
          aria-label="Conta ativa"
          value={activeSeller.id}
          onChange={(e) => {
            const next = connectedSellers.find((s) => s.id === e.target.value)
            if (next) setActiveSeller(next)
          }}
          className="h-8 rounded-md border border-border bg-card px-2 font-medium text-foreground"
        >
          {connectedSellers.map((s) => (
            <option key={s.id} value={s.id}>{s.ml_nickname}</option>
          ))}
        </select>
      )}

      {activeSeller && !naPaginaDeContas && (
        <Link href="/contas" className="ml-auto text-xs text-slate-500 hover:text-foreground">
          Gerenciar contas
        </Link>
      )}
    </div>
  )
}
