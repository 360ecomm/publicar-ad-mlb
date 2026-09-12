import { useEffect, useState } from "react"

/**
 * Devolve `value` só depois de `delayMs` sem mudança. Escrito à mão de
 * propósito: a fila busca sobre milhares de anúncios e não pode disparar
 * uma requisição por tecla como a tela de produtos faz.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}
