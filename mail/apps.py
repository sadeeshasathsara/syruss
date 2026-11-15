"""
Provider apps: the OAuth client each provider gave Syruss (Google Cloud's "Web application" client, Microsoft Entra's app
registration). Set from the admin app's Email › Provider apps tab, kept sealed in this service's database, and never
shown again: only whether one is set, its client id and (Microsoft) its tenant are public.

    get(provider)       {client_id, client_secret, tenant} or None; each falls back to GOOGLE_MAIL_CLIENT_* / MICROSOFT_MAIL_CLIENT_* (and _TENANT) in the environment
    configured(provider)
    describe(provider)   what the directory may say: whether it is set (and Microsoft's tenant); never the client id or secret
    load(store)         reads them at start (and after a change)
    save / clear        what the commands provider_app.set / provider_app.clear do

Accounts read the current app each time they sign in, so a rotated client secret takes effect at once.
"""
from typing import Any, Dict, Optional

from sqlalchemy import delete

from mail.config import settings
from mail.store import SettingRow, Store, seal, unseal

PROVIDERS = ("google", "microsoft")
_cache: Dict[str, Dict[str, Any]] = {}


def _key(provider: str) -> str:
    return f"app:{provider}"


def get(provider: str) -> Optional[Dict[str, Any]]:
    app = _cache.get(provider)
    if app:
        return app
    if provider == "google" and settings.GOOGLE_MAIL_CLIENT_ID and settings.GOOGLE_MAIL_CLIENT_SECRET:
        return {"client_id": settings.GOOGLE_MAIL_CLIENT_ID, "client_secret": settings.GOOGLE_MAIL_CLIENT_SECRET, "tenant": "", "from_environment": True}
    if provider == "microsoft" and settings.MICROSOFT_MAIL_CLIENT_ID and settings.MICROSOFT_MAIL_CLIENT_SECRET:
        return {"client_id": settings.MICROSOFT_MAIL_CLIENT_ID, "client_secret": settings.MICROSOFT_MAIL_CLIENT_SECRET,
                "tenant": settings.MICROSOFT_MAIL_TENANT.strip(), "from_environment": True}
    return None


def configured(provider: str) -> bool:
    return get(provider) is not None


def describe(provider: str) -> Dict[str, Any]:
    """What the directory says about a provider: public things only."""
    app = get(provider)
    if app is None:
        return {"configured": False}
    out: Dict[str, Any] = {"configured": True, "from_environment": bool(app.get("from_environment"))}   # never the client id or secret
    if provider == "microsoft":
        out["tenant"] = app.get("tenant") or "common"
    return out


async def load(store: Store) -> None:
    async with store.session() as s:
        from sqlalchemy import select
        rows = (await s.execute(select(SettingRow).where(SettingRow.key.like("app:%")))).scalars().all()
    _cache.clear()
    for row in rows:
        try:
            _cache[row.key.split(":", 1)[1]] = unseal(row.value)
        except ValueError:
            continue  # sealed with a key that is gone: treated as not set


async def save(store: Store, provider: str, client_id: str, client_secret: str, tenant: str = "") -> None:
    value = seal({"client_id": client_id, "client_secret": client_secret, "tenant": tenant})
    async with store.session() as s:
        row = await s.get(SettingRow, _key(provider))
        if row is None:
            s.add(SettingRow(key=_key(provider), value=value))
        else:
            row.value = value
        await s.commit()
    await load(store)


async def clear(store: Store, provider: str) -> None:
    async with store.session() as s:
        await s.execute(delete(SettingRow).where(SettingRow.key == _key(provider)))
        await s.commit()
    await load(store)
