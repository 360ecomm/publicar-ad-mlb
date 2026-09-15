# Regenerar UMA posição de imagem — spec aprovada (2026-09-12)

Decisões do Daniel sobre o passo 0 da tarefa. O plano de implementação está
em `docs/superpowers/plans/2026-09-12-regenerar-posicao.md`.

## Motivo

Quando uma posição falha no QA, hoje só há dois caminhos: publicar o anúncio
incompleto ou regenerar as **cinco** posições (`pipeline/generate_images`),
gastando cinco chamadas de IA para corrigir uma. A tela de revisão (bloco B)
precisa de um botão "gerar de novo" por posição.

## Restrições já decididas

- **Só posição não aprovada.** Regenerar uma posição já aprovada é recusado
  (409). Imagem aprovada não é substituída por trás do operador. Vale em
  `pending_image_approval`; a partir de `ready_to_publish` (Task 5,
  2026-09-15) a posição é desaprovada e o anúncio volta para revisão em vez
  de recusar.
- **Só em `pending_image_approval`.** Em qualquer outro status, 409 legível.
  **Atualização (2026-09-15, Task 5):** passou a aceitar também
  `ready_to_publish` — nesse status as 5 posições já estão aprovadas, então
  regenerar desaprova a posição pedida e devolve o anúncio a
  `pending_image_approval` na mesma transação do placeholder.
- **A posição 0 mantém o fallback da capa determinística** quando a IA falha,
  mesmo comportamento da geração completa.
- **Uma posição por chamada.**
- **Não** mexer em `bulk_generate_images` nem em `pipeline/generate_images`.
- O anúncio **fica em `pending_image_approval`** durante a regeneração. O
  worker nunca toca em `listing.status` nem em `listing.error_message`.
  `generating_images` reativaria o guard do worker completo, e os standbys
  `pending_ai_engine`/`pending_raw_photos` são retomados pelo beat com a
  geração **completa** — nunca usar nenhum dos três aqui.

## Decisões do passo 0

1. **Numeração 0 a 4** na API, igual ao `sort_order` do `ImageOut`. A tela
   traduz para 1 a 5.
2. **Trava = a própria linha nova.** O endpoint insere um placeholder
   `ListingImage(status="generating", kind=<kind da posição>, sort_order=pos,
   approved=False)` e faz commit **antes** de enfileirar. Índice único
   parcial `uq_listing_images_generating_slot (listing_id, sort_order) WHERE
   status = 'generating'`: o segundo clique falha no commit e vira 409
   "Regeneração em andamento na posição N; aguarde." Migração sobre
   `f7b3e9c1d2a5` (head único confirmado em 2026-09-12).
   `ListingJob` **não** é usado: a tabela não recebe INSERT em lugar nenhum.
3. **Linha anterior apagada no sucesso, mantida na falha.** Sucesso =
   a nova linha subiu ao ML (`status="uploaded"`, por IA ou pelo fallback da
   capa). Apagam-se as linhas **não aprovadas** que ocupavam a posição
   **antes** da regeneração começar (ids capturados no início; linhas criadas
   pela própria regeneração, como o fallback, nunca são apagadas). Nunca
   apagar linha aprovada — em `pending_image_approval` a chamada já foi
   recusada antes; vindo de `ready_to_publish` (Task 5, 2026-09-15) a linha
   já foi desaprovada antes de chegar aqui, então também nunca está entre as
   apagadas. O `asset_key` de cada linha apagada vai para o log.
   - Falha do motor (nenhuma imagem produzida): placeholder vira
     `status="generation_failed"` com o motivo em `validation_error`; a
     anterior permanece.
   - IA produziu, QA reprovou: placeholder vira `validation_failed` com
     `asset_key` (evidência para o humano, como na geração completa); a
     anterior **permanece** (não foi sucesso). Ficam duas linhas não
     aprovadas na posição até a próxima regeneração, que limpa as duas.
4. **Bloquear `approve_images` e `bulk_approve_images` com 409** enquanto
   houver linha `generating` no anúncio, com mensagem própria
   ("Regeneração em andamento na posição N; aguarde a conclusão antes de
   aprovar."), **não** o genérico "estado inválido".
5. **Vocabulário de `ListingImage.status`:** `generating` (placeholder, já
   era o default do model e não era usado por ninguém) e
   **`generation_failed`** (regeneração que não produziu imagem).
   `validation_failed` continua significando "imagem saiu e reprovou no QA".
   `failed` fica exclusivo de `Listing.status`.
6. **Custo:** a chamada de regeneração sai no log `ai_cost` com
   `task="image_edit_regen"`. Obrigatório: é o número que decide entre
   melhorar o prompt ou seguir regenerando.
7. **Copy do card na posição 2:** o LLM é chamado só quando a posição
   regenerada é a 2, e gera texto novo. **Não** persistir copy nesta tarefa.
   Pendência: a tela precisará avisar que regenerar a posição 2 pode mudar
   o texto.

## Contrato

```
POST /api/v1/listings/{listing_id}/images/positions/{posicao}/regenerate
  202 → ImageOut do placeholder (status="generating")
  404 → anúncio não é do seller ativo
  409 → status fora de pending_image_approval/ready_to_publish | posição
        aprovada (em pending_image_approval) | regeneração em andamento
  422 → posição fora de 0..4
```

A partir de `ready_to_publish` (Task 5, 2026-09-15): a posição pedida é
desaprovada e o anúncio volta para `pending_image_approval`, na mesma
transação do placeholder.

Task Celery `app.workers.tasks.image_tasks.regenerate_position(listing_id,
image_id)`, fila `images` (mesma rota das outras tasks do módulo).

## Guards do worker

- Placeholder inexistente ou com `status != "generating"` → `skipped`
  (retry ou dispatch duplo).
- `listing.status != "pending_image_approval"` (aprovação venceu a corrida)
  → apaga o placeholder, `skipped`, nada é gerado.
- Fotos brutas ausentes no bucket → `generation_failed` com motivo.
- `ImageEngineUnavailableError` → rollback, `generation_failed` com motivo.
  **Nunca** `pending_ai_engine`.

## Prova

- Os testes existentes de geração de imagens passam **sem uma linha
  alterada**. Se algum precisar mudar, parar e reportar.
- Linhas de base: **482 passed, 56 skipped** (sem `TEST_DATABASE_URL`) e
  **538 passed** (com ela).

## Registrar (não corrigir nesta branch)

- O comentário em `listing_job.py` e na migração `f7b3e9c1d2a5` diz que a
  tabela "recebe INSERT num único ponto do código". Zero inserções: tabela
  morta. Fila de documentação.
- A tela de revisão precisa mostrar `status` por imagem (`generating`,
  `generation_failed`, `validation_failed`) e lidar com duas linhas não
  aprovadas na mesma posição depois de uma reprovação do QA.
