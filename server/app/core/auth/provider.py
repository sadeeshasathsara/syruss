from abc import ABC, abstractmethod
from fastapi import HTTPException
from starlette.requests import HTTPConnection
from app.core.auth.context import Identity


class BaseIAMProvider(ABC):
    """Contract for the identity provider: turn a request into who is calling, or raise 401."""

    @abstractmethod
    async def authenticate(self, request: HTTPConnection) -> Identity:
        ...


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})
