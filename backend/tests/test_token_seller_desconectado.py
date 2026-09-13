"""Guarda de `get_valid_access_token`: conta desconectada nunca chega ao Fernet.

Sem a guarda, um worker que tocasse num anuncio de uma conta desconectada
(token NULL) morreria com erro de criptografia — sintoma longe da causa. A
guarda recusa seller inativo OU sem token com `SellerDisconnectedError`,
antes de qualquer `decrypt_value`.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _seller(**kw):
    base = dict(
        ml_nickname="LOJA",
        is_active=True,
        access_token_enc="cifrado-a",
        refresh_token_enc="cifrado-r",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestGuardaDeContaDesconectada:
    @pytest.mark.asyncio
    async def test_inativo_recusa_antes_do_fernet(self):
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError) as exc:
                await get_valid_access_token(_seller(is_active=False), db=AsyncMock())
        decrypt.assert_not_called()
        assert "LOJA" in str(exc.value)
        assert "desconectada" in str(exc.value)

    @pytest.mark.asyncio
    async def test_ativo_sem_access_token_recusa_antes_do_fernet(self):
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError):
                await get_valid_access_token(_seller(access_token_enc=None), db=AsyncMock())
        decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_ativo_sem_refresh_token_recusa_mesmo_com_access_valido(self):
        """O access expira em horas; sem refresh a conta esta' morta em breve
        e a renovacao explodiria no Fernet. Recusar ja."""
        from app.services.publish_service import SellerDisconnectedError, get_valid_access_token

        with patch("app.services.publish_service.decrypt_value") as decrypt:
            with pytest.raises(SellerDisconnectedError):
                await get_valid_access_token(_seller(refresh_token_enc=None), db=AsyncMock())
        decrypt.assert_not_called()

    @pytest.mark.asyncio
    async def test_ativo_com_token_valido_devolve_o_token(self):
        from app.services.publish_service import get_valid_access_token

        with patch("app.services.publish_service.decrypt_value", return_value="tok-claro") as decrypt:
            token = await get_valid_access_token(_seller(), db=AsyncMock())
        assert token == "tok-claro"
        decrypt.assert_called_once_with("cifrado-a")

    def test_erro_e_runtimeerror_para_os_workers_tratarem_como_falha(self):
        """Os workers ja tratam RuntimeError como falha do listing; a guarda
        nao pode introduzir um tipo que escape desse tratamento."""
        from app.services.publish_service import SellerDisconnectedError

        assert issubclass(SellerDisconnectedError, RuntimeError)
