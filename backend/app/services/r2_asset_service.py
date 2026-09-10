"""Ativos de imagem gerados por IA num bucket R2 dedicado (write-back).

O banco guarda so a referencia (`ListingImage.asset_key`); os bytes vivem no
R2, organizados de forma LEGIVEL por humano:

    {apelido_ml_do_seller}/{sku}/{kind}-{AAAAMMDD-HHMMSS}-{4hex}.jpg
    ex.: CAFE085/37/cover_ai-20260910-234512-ab12.jpg

Apelido do seller no Mercado Livre (`Seller.ml_nickname`), nao o uuid
interno; sem camada de listing, porque essa relacao ja vive no banco. O
timestamp (UTC) da a ordem cronologica pelo nome; o sufixo de 4 hex so
desempata duas geracoes no mesmo segundo (retentativa rapida).

Bucket DISTINTO do de fotos brutas dos sellers (que e' so leitura publica):
este tem credencial de escrita, configurada por `R2_ASSET_BUCKET_*`.

Momento do write-back: na GERACAO de cada posicao (aprovada ou reprovada
no QA), o mesmo momento em que o candidato ja sobe ao CDN do ML. Sem
credencial, ou com falha do R2, a geracao NAO cai: a linha nasce com
`asset_key=None` e o log avisa — perder a posicao por causa do arquivo seria
pior que perder o arquivo.

Quem le: `cover_variant_service` e `specs_variant_service`, que partem dos
bytes exatos da capa publicada (`load_candidate_bytes`). boto3 e' sincrono;
as chamadas rodam em `asyncio.to_thread` para nao travar o loop.
"""
import asyncio
import logging
import re
import secrets
from datetime import datetime, timezone

import boto3
from sqlalchemy import select

from app.config import get_settings

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def seller_slug_from(nickname) -> str:
    """Apelido do ML saneado para caminho; `seller` se vier vazio."""
    limpo = _SAFE.sub("_", (nickname or "").strip())
    return limpo or "seller"


def asset_key_for(*, seller_slug: str, sku: str, kind: str, when: datetime | None = None,
                  token: str | None = None, ext: str = "jpg") -> str:
    """Monta a chave. UNICO ponto que conhece o formato do caminho."""
    when = when or datetime.now(timezone.utc)
    carimbo = when.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sufixo = token or secrets.token_hex(2)
    sku_safe = _SAFE.sub("_", str(sku)) or "sku"
    return f"{seller_slug}/{sku_safe}/{kind}-{carimbo}-{sufixo}.{ext}"


async def seller_slug_for(db, seller_id) -> str:
    """Apelido do ML do seller, saneado. Cai em `seller` se nao achar."""
    from app.models.seller import Seller

    nickname = (
        await db.execute(select(Seller.ml_nickname).where(Seller.id == seller_id))
    ).scalar_one_or_none()
    return seller_slug_from(nickname)


class R2AssetStore:
    def __init__(self, settings) -> None:
        self._s = settings
        self._client = None

    @property
    def configured(self) -> bool:
        return all((
            self._s.r2_asset_bucket_name,
            self._s.r2_asset_bucket_endpoint,
            self._s.r2_asset_bucket_access_key_id,
            self._s.r2_asset_bucket_secret_access_key,
        ))

    def _cli(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._s.r2_asset_bucket_endpoint,
                aws_access_key_id=self._s.r2_asset_bucket_access_key_id,
                aws_secret_access_key=self._s.r2_asset_bucket_secret_access_key,
                region_name="auto",
            )
        return self._client

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._cli().put_object,
            Bucket=self._s.r2_asset_bucket_name, Key=key, Body=data, ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self._cli().get_object, Bucket=self._s.r2_asset_bucket_name, Key=key)
        return obj["Body"].read()


_store: R2AssetStore | None = None


def get_asset_store() -> R2AssetStore:
    global _store
    if _store is None:
        _store = R2AssetStore(get_settings())
    return _store


async def store_candidate_bytes(data: bytes, *, db, seller_id, sku: str, kind: str, ext: str = "jpg") -> str | None:
    """Grava os bytes no R2 e devolve a chave; None (com aviso) se nao houver
    credencial ou o R2 falhar. Nunca levanta."""
    store = get_asset_store()
    if not store.configured:
        logger.warning(
            "r2_asset sku=%s kind=%s result=sem_credencial (R2_ASSET_BUCKET_* ausente): bytes nao persistidos",
            sku, kind,
        )
        return None
    try:
        slug = await seller_slug_for(db, seller_id)
        key = asset_key_for(seller_slug=slug, sku=sku, kind=kind, ext=ext)
        await store.put(key, data, _CONTENT_TYPES.get(ext, "application/octet-stream"))
    except Exception as exc:
        logger.error("r2_asset sku=%s kind=%s result=falha_no_put reason=%s", sku, kind, exc)
        return None
    logger.info("r2_asset sku=%s kind=%s key=%s bytes=%s result=gravado", sku, kind, key, len(data))
    return key


async def load_candidate_bytes(image) -> bytes | None:
    """Bytes de uma `ListingImage` pela `asset_key`; None se nao houver chave,
    credencial, ou o objeto nao existir mais."""
    key = getattr(image, "asset_key", None)
    if not key:
        return None
    store = get_asset_store()
    if not store.configured:
        logger.warning("r2_asset key=%s result=sem_credencial_para_ler", key)
        return None
    try:
        return await store.get(key)
    except Exception as exc:
        logger.error("r2_asset key=%s result=falha_no_get reason=%s", key, exc)
        return None
