"use client"

import { useCallback, useEffect, useRef } from "react"
import { ReviewClock } from "@/lib/review-clock"

/**
 * Cronômetro da revisão: conta a partir de `active` (as imagens apareceram)
 * até o clique em aprovar, e pausa enquanto a aba está fora de foco
 * (`document.visibilityState`). Não dispara re-render: quem precisa do valor
 * chama `elapsedSeconds()` na hora de enviar.
 */
export function useReviewTimer(active: boolean): () => number {
  const clock = useRef<ReviewClock | null>(null)
  if (clock.current === null) clock.current = new ReviewClock()

  useEffect(() => {
    if (!active) return
    const relogio = clock.current!
    const sincronizar = () => {
      if (document.visibilityState === "visible") relogio.start(Date.now())
      else relogio.pause(Date.now())
    }
    sincronizar()
    document.addEventListener("visibilitychange", sincronizar)
    return () => {
      document.removeEventListener("visibilitychange", sincronizar)
      relogio.pause(Date.now())
    }
  }, [active])

  return useCallback(() => clock.current!.elapsedSeconds(Date.now()), [])
}
