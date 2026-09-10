"""Copia os blobs `listing_images.image_bytes` existentes para o bucket R2 de
ativos e preenche `asset_key`. Idempotente: pula linhas que ja tem chave.

Rodar DENTRO do container do backend, com as variaveis R2_ASSET_BUCKET_*
carregadas, DEPOIS da migration 5a1c7e2d9b04 e ANTES da 7b2d9f4e1c58:

    docker compose -p publicar-ad-mlb -f docker-compose.prod.yml exec -T \
        -e PYTHONPATH=/app -w /app backend python scripts/migrate_image_bytes_to_r2.py

Usa SQL cru na coluna `image_bytes` porque o model ja nao a mapeia. Le e
grava linha a linha (blobs de 300-600 KB; 27 linhas em producao), e confere
cada objeto com um GET antes de gravar a chave — a coluna so sera apagada
quando toda linha com blob tiver chave conferida.
"""
import asyncio
import sys

from sqlalchemy import text


async def main() -> int:
    from app.database import worker_session
    from app.services.r2_asset_service import get_asset_store, store_candidate_bytes

    store = get_asset_store()
    if not store.configured:
        print("R2_ASSET_BUCKET_* nao configurado: nada feito", file=sys.stderr)
        return 2

    ok = pulados = falhas = 0
    async with worker_session() as db:
        rows = (await db.execute(text(
            "select li.id, li.listing_id, li.kind, li.image_bytes, l.seller_id, l.sku_external_id "
            "from listing_images li join listings l on l.id = li.listing_id "
            "where li.image_bytes is not null and li.asset_key is null order by li.created_at"
        ))).all()
        print(f"{len(rows)} linha(s) com blob e sem chave")
        for (img_id, listing_id, kind, blob, seller_id, sku) in rows:
            key = await store_candidate_bytes(
                bytes(blob), seller_id=seller_id, sku=sku or "?", listing_id=listing_id, kind=kind
            )
            if key is None:
                falhas += 1
                print(f"  FALHA {img_id} kind={kind}")
                continue
            lido = await store.get(key)
            if lido != bytes(blob):
                falhas += 1
                print(f"  FALHA (conferencia) {img_id} key={key}")
                continue
            await db.execute(text("update listing_images set asset_key = :k where id = :i"), {"k": key, "i": img_id})
            await db.commit()
            ok += 1
            print(f"  ok {img_id} kind={kind} {len(blob)} bytes -> {key}")
        restantes = (await db.execute(text(
            "select count(*) from listing_images where image_bytes is not null and asset_key is null"
        ))).scalar_one()
    print(f"migrados={ok} pulados={pulados} falhas={falhas} restantes_sem_chave={restantes}")
    return 0 if falhas == 0 and restantes == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
