from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_settings
from app.core.dependencies import get_db, get_current_user
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.services.auth_service import AuthService
from app.services.ml_oauth_service import MLOAuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    return await AuthService(db).login(request.email, request.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await AuthService(db).refresh(request.refresh_token)


@router.get("/ml/connect")
async def ml_connect(current_user=Depends(get_current_user)):
    auth_url = await MLOAuthService().get_authorization_url(user_id=str(current_user.id))
    return {"auth_url": auth_url}


@router.get("/ml/callback")
async def ml_callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    """Callback do OAuth do Mercado Livre.

    Sem frontend configurado, devolve JSON: redirecionar para uma tela que nao
    existe transformaria uma conexao bem-sucedida num erro de navegador. Com
    `FRONTEND_URL` definida, volta a redirecionar — o terreno ja fica pronto
    para quando o frontend entrar, sem quebrar o caminho de hoje.

    A tela de contas (/contas) e' quem conecta e desconecta; e' para la que o
    operador volta.
    """
    await MLOAuthService().handle_callback(code, state, db)

    frontend_url = get_settings().frontend_url
    if frontend_url:
        return RedirectResponse(
            url=f"{frontend_url.rstrip('/')}/contas?ml_connected=true"
        )
    return {"status": "connected"}
