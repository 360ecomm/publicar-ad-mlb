# Contas: página própria, seletor fixo no topo, desconexão e retorno do OAuth

Bloco D, tarefa 3. Decisões do Daniel em 2026-09-13, a partir das primeiras
impressões usando a aplicação no ar. Só frontend, exceto a desconexão (item 4)
e o destino do retorno do OAuth (item 5).

## Contexto

- `contexts/SellerContext.tsx` já tem a lista de sellers, a conta ativa,
  `setActiveSeller` e persistência em `localStorage`; `apiFetch` já manda
  `X-Seller-ID`. Falta interface.
- O painel de contas existe em `app/(dashboard)/page.tsx` mas está
  inalcançável: `app/page.tsx` ocupa a raiz e redireciona para `/listings`.
  É a causa do aviso `Failed to copy traced files` no `next build`.
- Já existe um seletor de conta no rodapé do menu lateral, que abre
  recolhido: o seletor vira só um ícone. Substituir, não duplicar.
- O contexto escolhe `data[0]` como ativo sem olhar `is_active`. Com uma conta
  desconectada em primeiro lugar, toda chamada daria 403. Defeito atual.

## Decisões

1. **Rota própria:** o painel vai para `(dashboard)/contas/page.tsx`.
   `app/page.tsx` continua redirecionando para `/listings` (a fila é a porta
   de entrada do trabalho). O aviso do build tem que sumir.
2. **Menu lateral:** "Anúncios" aponta direto para `/listings`; item novo
   "Contas" → `/contas`; o link "Voltar ao kanban" de `/listings/attributes`
   ganha texto e destino corretos (a fila, `/listings`).
3. **Seletor fixo no topo do conteúdo**, em `(dashboard)/layout.tsx`, visível
   em toda tela do painel. Mostra a conta ativa e troca via `setActiveSeller`.
   **Trocar de conta recarrega os dados da tela**: `queryClient.resetQueries()`
   na troca, com `localStorage` já atualizado ANTES do reset, para o refetch
   carregar o `X-Seller-ID` novo e a tela não exibir dados da conta anterior
   em nenhum instante. Com uma conta só: aparece, sem menu de troca. Nenhuma:
   mensagem com link para conectar. **O seletor só oferece contas ativas.**
4. **Desconectar seller: apaga só o token, mantém o histórico.** Migração
   tornando `access_token_enc` e `refresh_token_enc` anuláveis (string vazia
   codificaria "sem token" num campo que declara não aceitar ausência).
   `is_active = False` é a marca de desconectada (já recusada pelo
   `X-Seller-ID`, já revertida a `True` pelo callback do OAuth). Guarda em
   `get_valid_access_token`: seller inativo ou sem token → erro claro, nunca
   erro de criptografia dentro de um worker. Botão em `/contas` com
   confirmação: o histórico fica, os anúncios param de ser publicados,
   reconectar exige autorizar de novo no ML. Desconectada a conta ativa,
   trocar para outra ou limpar a seleção. `/contas` lista as desconectadas
   com "Reconectar".
5. **OAuth em nova aba** (`window.open`, `noopener`), para o operador não
   perder a aplicação. **Destino do retorno: `/contas?ml_connected=true`**
   (o botão de `/settings` continua existindo). `FRONTEND_URL` em produção
   fica para uma tarefa de deploy própria.

## Testes exigidos

- Guarda de `get_valid_access_token`: inativo → erro claro sem Fernet; ativo
  sem token → idem.
- Migração: `upgrade` e `downgrade` em Postgres real.
- Desconectar mantém anúncios, produtos, imagens e eventos: contagens antes
  e depois.
- Reconectar pelo OAuth volta a conta a ativa com token novo.
- `npx tsc --noEmit`, `npx next lint`, `npx next build` sem erro e sem o
  aviso do item 1.
- Suíte do backend sem e com `TEST_DATABASE_URL`.
- Prova no navegador, contra o backend local, com um segundo seller SEMEADO
  (nunca o real), apagado no fim com contagens antes e depois.
