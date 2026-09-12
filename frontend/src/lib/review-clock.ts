/**
 * Cronômetro da revisão humana, sem dependência de DOM (testável em Node).
 *
 * Conta só enquanto está "rodando": a tela chama `start` quando as imagens
 * aparecem e quando a aba volta ao foco, `pause` quando a aba perde o foco.
 * O total vai em `review_seconds` na aprovação. Valores altos NÃO são
 * descartados: revisão que demorou de verdade é o dado que justifica criar
 * um perfil para aquela vertical.
 */
export class ReviewClock {
  private accumulatedMs = 0
  private runningSince: number | null = null

  /** Começa (ou retoma) a contar. Chamar de novo enquanto roda não reinicia nada. */
  start(nowMs: number): void {
    if (this.runningSince === null) this.runningSince = nowMs
  }

  /** Congela o trecho atual. Chamar sem estar rodando é inócuo. */
  pause(nowMs: number): void {
    if (this.runningSince !== null) {
      this.accumulatedMs += Math.max(0, nowMs - this.runningSince)
      this.runningSince = null
    }
  }

  get running(): boolean {
    return this.runningSince !== null
  }

  elapsedMs(nowMs: number): number {
    const emCurso = this.runningSince === null ? 0 : Math.max(0, nowMs - this.runningSince)
    return this.accumulatedMs + emCurso
  }

  /** Segundos inteiros (arredondados, nunca negativos) — o formato de `review_seconds`. */
  elapsedSeconds(nowMs: number): number {
    return Math.max(0, Math.round(this.elapsedMs(nowMs) / 1000))
  }
}
