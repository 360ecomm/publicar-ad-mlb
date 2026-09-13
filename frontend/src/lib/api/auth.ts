import { apiFetch } from "./client"

export interface LoginResponse {
  access_token: string
  refresh_token: string
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  })
}

export async function getMLConnectUrl(): Promise<string> {
  const res = await apiFetch<{ auth_url: string }>("/api/v1/auth/ml/connect")
  return res.auth_url
}

export async function openMLAuthorization(): Promise<"nova-aba" | "mesma-aba"> {
  // A aba abre ANTES do await: window.open só e' permitido dentro da
  // ativacao do clique, e o round-trip que busca a URL pode estourar essa
  // janela (Safari bloqueia quase sempre depois de um await). Aberta em
  // branco agora, recebe a URL quando ela chegar.
  const w = window.open("", "_blank")
  try {
    const url = await getMLConnectUrl()
    if (w) {
      try { w.opener = null } catch { /* mesma origem em about:blank; nao deve falhar */ }
      w.location.href = url
      return "nova-aba"
    }
    // Pop-up bloqueado: pior que a aba nova, melhor que um botao que parece morto.
    window.location.href = url
    return "mesma-aba"
  } catch (err) {
    w?.close()
    throw err
  }
}
