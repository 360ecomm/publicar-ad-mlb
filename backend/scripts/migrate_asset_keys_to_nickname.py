"""Renomeia os objetos do bucket de ativos do padrao antigo
`{seller_uuid}/{sku}/{listing_uuid}/{kind}-{hash}.jpg` para o novo
`{apelido_ml}/{sku}/{kind}-{AAAAMMDD-HHMMSS}-{4hex}.jpg`.

Ordem de seguranca por objeto (nunca apaga antes de confirmar o novo):
  1. CopyObject para a chave nova (mesmo bucket);
  2. GET dos dois e comparacao byte a byte;
  3. UPDATE de `asset_key` no banco;
  4. DeleteObject da chave antiga.
Idempotente: linhas ja no padrao novo sao puladas. O timestamp da chave nova
e' o `created_at` da linha (UTC), para a ordem cronologica pelo nome refletir
a geracao real, nao a data desta migracao.

Rodar DENTRO do container do backend, com R2_ASSET_BUCKET_* carregado:
    docker compose -p publicar-ad-mlb -f docker-compose.prod.yml exec -T \
        -e PYTHONPATH=/app -w /app backend python scripts/migrate_asset_keys_to_nickname.py
"""
import asyncio
import re
import sys
from datetime import timezone

from sqlalchemy import text

_PADRAO_ANTIGO = re.compile(r"^[0-9a-f-]{36}/")


async def main() -> int:
    from app.services.r2_asset_service import asset_key_for, get_asset_store, seller_slug_from

    store = get_asset_store()
    if not store.configured:
        print("R2_ASSET_BUCKET_* nao configurado: nada feito", file=sys.stderr)
        return 2
    cli = store._cli()
    bucket = store._s.r2_asset_bucket_name

    from app.database import worker_session

    ok = pulados = falhas = 0
    async with worker_session() as db:
        rows = (await db.execute(text(
            "select li.id, li.asset_key, li.kind, li.created_at, l.sku_external_id, s.ml_nickname "
            "from listing_images li join listings l on l.id = li.listing_id join sellers s on s.id = l.seller_id "
            "where li.asset_key is not null order by li.created_at"
        ))).all()
        print(f"{len(rows)} linha(s) com asset_key")
        for (img_id, chave_antiga, kind, created_at, sku, nickname) in rows:
            if not _PADRAO_ANTIGO.match(chave_antiga):
                pulados += 1
                continue
            quando = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            chave_nova = asset_key_for(seller_slug=seller_slug_from(nickname), sku=sku or "sku", kind=kind, when=quando)
            try:
                await asyncio.to_thread(cli.copy_object, Bucket=bucket, Key=chave_nova,
                                        CopySource={"Bucket": bucket, "Key": chave_antiga}, MetadataDirective="COPY")
                antigo = await store.get(chave_antiga)
                novo = await store.get(chave_nova)
                if antigo != novo:
                    falhas += 1
                    print(f"  FALHA (conferencia) {img_id} {chave_antiga} -> {chave_nova}")
                    continue
                await db.execute(text("update listing_images set asset_key = :k where id = :i"), {"k": chave_nova, "i": img_id})
                await db.commit()
                await asyncio.to_thread(cli.delete_object, Bucket=bucket, Key=chave_antiga)
                ok += 1
                print(f"  ok {kind:<20} {len(novo):>7} bytes  {chave_antiga}  ->  {chave_nova}")
            except Exception as exc:
                falhas += 1
                print(f"  FALHA {img_id} {chave_antiga}: {exc}")
        restantes = (await db.execute(text(
            "select count(*) from listing_images where asset_key ~ '^[0-9a-f-]{36}/'"
        ))).scalar_one()
    print(f"migrados={ok} ja_no_padrao_novo={pulados} falhas={falhas} restantes_no_padrao_antigo={restantes}")
    return 0 if falhas == 0 and restantes == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
