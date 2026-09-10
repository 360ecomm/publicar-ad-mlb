import httpx
import boto3

from app.core.security import decrypt_value

ML_ITEMS_URL = "https://api.mercadolibre.com/items"


async def write_back_images(db, listing, seller_image_config, access_token: str) -> None:
    """Best-effort: baixa cada imagem publicada do CDN do ML e regrava no
    bucket do seller. Nunca levanta exceção — falhas ficam registradas em
    ListingImage.r2_write_status, e a publicação (que já aconteceu antes
    desta função rodar) nunca é revertida por causa disso."""
    from sqlalchemy import select
    from app.models.listing_image import ListingImage

    images = (
        await db.execute(
            select(ListingImage)
            .where(
                ListingImage.listing_id == listing.id, ListingImage.approved == True
            )
        )
    ).scalars().all()

    has_write_config = bool(
        seller_image_config
        and seller_image_config.write_bucket_name
        and seller_image_config.write_endpoint_url
        and seller_image_config.write_access_key_id_enc
        and seller_image_config.write_secret_access_key_enc
    )
    if not has_write_config:
        for img in images:
            img.r2_write_status = "skipped_no_config"
        await db.commit()
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{ML_ITEMS_URL}/{listing.mlb_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        for img in images:
            img.r2_write_status = "failed"
        await db.commit()
        return

    pictures_by_id = {p["id"]: p for p in resp.json().get("pictures", [])}

    s3 = boto3.client(
        "s3",
        endpoint_url=seller_image_config.write_endpoint_url,
        aws_access_key_id=decrypt_value(seller_image_config.write_access_key_id_enc),
        aws_secret_access_key=decrypt_value(seller_image_config.write_secret_access_key_enc),
    )

    for n, img in enumerate(images, start=1):
        picture = pictures_by_id.get(img.ml_picture_id)
        if picture is None:
            img.r2_write_status = "failed"
            continue
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                photo_resp = await client.get(picture["secure_url"])
            photo_resp.raise_for_status()
            key = f"anuncios/{listing.mlb_id}-{n}.jpg"
            s3.put_object(
                Bucket=seller_image_config.write_bucket_name,
                Key=key,
                Body=photo_resp.content,
                ContentType="image/jpeg",
            )
            img.url_r2 = key
            img.r2_write_status = "success"
        except Exception:
            img.r2_write_status = "failed"

    await db.commit()
