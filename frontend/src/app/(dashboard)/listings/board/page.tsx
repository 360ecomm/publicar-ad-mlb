import Link from "next/link"
import { Plus } from "lucide-react"
import { PipelineBoard } from "@/components/listings/PipelineBoard"
import { Button } from "@/components/ui/button"

// Rota transitória: o quadro só existe aqui porque as ações em massa ainda
// vivem nele. Sai na tarefa 3, quando a fila ganhar seleção própria.
export default function ListingsBoardPage() {
  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Quadro de anúncios</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Tela transitória, só para as ações em massa; sai quando a{" "}
            <Link href="/listings" className="underline underline-offset-4">
              fila
            </Link>{" "}
            ganhar seleção. Atualiza automaticamente a cada 8 segundos.
          </p>
        </div>
        <Button asChild size="sm">
          <Link href="/listings/new">
            <Plus className="w-4 h-4 mr-1" />
            Novo anúncio
          </Link>
        </Button>
      </div>
      <PipelineBoard />
    </div>
  )
}
