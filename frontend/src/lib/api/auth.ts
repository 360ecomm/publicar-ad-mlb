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

/**
 * Abre a autorização do ML em aba nova, sem dar à aba nova acesso a esta
 * (`opener = null`, o mesmo efeito de "noopener" — mas com o handle, que a
 * forma "noopener" do window.open nunca devolve). Se o navegador bloquear o
 * pop-up (o await da URL pode estourar a janela de ativação do clique),
 * cai na navegação na mesma aba: pior que a aba nova, melhor que um botão
 * que parece morto.
 */
export async function openMLAuthorization(): Promise<"nova-aba" | "mesma-aba"> {
  const url = await getMLConnectUrl()
  const w = window.open(url, "_blank")
  if (w) {
    w.opener = null
    return "nova-aba"
  }
  window.location.href = url
  return "mesma-aba"
}
