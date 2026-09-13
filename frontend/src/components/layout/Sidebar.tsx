"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import {
  Settings, LogOut, Tag, ChevronLeft, ChevronRight,
  LayoutDashboard, Upload, Sun, Moon, Package,
  ShoppingBag, type LucideIcon,
} from "lucide-react"
import { clearAuth } from "@/lib/api/client"
import { useTheme } from "@/contexts/ThemeContext"

interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  exact?: boolean
}

const NAV_ITEMS: NavItem[] = [
  // A fila e' a porta de entrada do trabalho; `/` so redireciona pra ca.
  // Sem `exact`: /listings/attributes e /listings/{id}/... tambem destacam.
  { href: "/listings", label: "Anúncios", icon: LayoutDashboard },
  { href: "/contas", label: "Contas", icon: ShoppingBag },
  { href: "/products", label: "Produtos", icon: Package },
  { href: "/import", label: "Importar anúncios", icon: Upload },
  { href: "/settings", label: "Configurações", icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()
  const [collapsed, setCollapsed] = useState(true)
  const { theme, toggleTheme } = useTheme()

  const handleLogout = () => {
    clearAuth()
    router.push("/login")
  }

  return (
    <aside
      className={`${
        collapsed ? "w-14" : "w-56"
      } bg-slate-900 text-white flex flex-col min-h-screen flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out`}
    >
      {/* Logo */}
      <div className="h-14 border-b border-slate-700 flex items-center flex-shrink-0 px-3 gap-2">
        <Tag className="w-5 h-5 text-yellow-400 flex-shrink-0" />
        <span className={`font-bold text-sm whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-300 ease-in-out ${collapsed ? "max-w-0 opacity-0" : "max-w-xs opacity-100"}`}>
          Publicar AD <span className="text-yellow-400">MLB</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon, exact }) => {
          const isActive = exact ? pathname === href : (pathname === href || pathname.startsWith(href + "/"))
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={`flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white hover:bg-slate-800"
              }`}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className={`whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-300 ease-in-out ${collapsed ? "max-w-0 opacity-0" : "max-w-xs opacity-100"}`}>
                {label}
              </span>
            </Link>
          )
        })}
      </nav>

      {/* Rodapé: logout + tema + toggle (a conta ativa mora na barra do topo, AccountBar) */}
      <div className="p-2 border-t border-slate-700 space-y-1">
        <button
          onClick={handleLogout}
          title={collapsed ? "Sair" : undefined}
          className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors w-full"
        >
          <LogOut className="w-4 h-4 flex-shrink-0" />
          <span className={`whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-300 ease-in-out ${collapsed ? "max-w-0 opacity-0" : "max-w-xs opacity-100"}`}>
            Sair
          </span>
        </button>

        <button
          onClick={toggleTheme}
          title={theme === "dark" ? "Mudar para tema claro" : "Mudar para tema escuro"}
          className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors w-full"
        >
          {theme === "dark" ? <Sun className="w-4 h-4 flex-shrink-0" /> : <Moon className="w-4 h-4 flex-shrink-0" />}
          <span className={`whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-300 ease-in-out ${collapsed ? "max-w-0 opacity-0" : "max-w-xs opacity-100"}`}>
            {theme === "dark" ? "Tema claro" : "Tema escuro"}
          </span>
        </button>

        <button
          onClick={() => setCollapsed((v) => !v)}
          title={collapsed ? "Expandir menu" : "Recolher menu"}
          className="flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-slate-500 hover:text-white hover:bg-slate-800 transition-colors w-full"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4 flex-shrink-0" />
          ) : (
            <>
              <ChevronLeft className="w-4 h-4 flex-shrink-0" />
              <span className="whitespace-nowrap overflow-hidden transition-[max-width,opacity] duration-300 ease-in-out max-w-xs opacity-100">
                Recolher
              </span>
            </>
          )}
        </button>
      </div>
    </aside>
  )
}
