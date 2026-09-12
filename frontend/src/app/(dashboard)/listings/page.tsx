import Link from "next/link"
import { Plus } from "lucide-react"
import { WorkQueue } from "@/components/listings/WorkQueue"
import { Button } from "@/components/ui/button"

export default function ListingsPage() {
  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Anúncios</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Fila de trabalho: clique na linha para ir direto à etapa que espera ação.
          </p>
        </div>
        <Button asChild size="sm">
          <Link href="/listings/new">
            <Plus className="w-4 h-4 mr-1" />
            Novo anúncio
          </Link>
        </Button>
      </div>
      <WorkQueue />
    </div>
  )
}
