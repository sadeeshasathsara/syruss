"""
Memory (privacy follows the run's origin: personal memory only for the person it's about).
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.models.memory import Memory
from app.models.outbox import AgentEventOutbox
from app.services import memory_service
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit


router = APIRouter()


async def _actor(ws: Workspace) -> Dict[str, Any]:
    """Who the run is for: a person chatting (with their conversation), or nobody in particular."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    return (event.actor or {}) if event else {}


class RememberBody(BaseModel):
    scope: str = Field("workspace", pattern="^(workspace|personal)$")
    kind: str = Field(..., pattern="^(lesson|observation|preference|fact)$")
    content: str = Field(..., min_length=3, max_length=2000)
    importance: float = Field(0.5, ge=0, le=1)
    data: Dict[str, Any] = Field(default_factory=dict)
    document_ids: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None


@router.post("/memories", status_code=201)
async def remember(body: RememberBody, ws: Workspace = Depends(get_workspace)):
    actor = await _actor(ws)
    user_id = None
    if body.scope == "personal":
        if actor.get("type") != "user" or actor.get("source") != "chat":
            raise HTTPException(status_code=403, detail="Personal memories come only from the person's own conversations with you.")
        if body.kind not in ("preference", "fact"):
            raise HTTPException(status_code=422, detail="Personal memories are preferences or facts about the person.")
        user_id = actor.get("id")
    elif body.kind == "preference":
        raise HTTPException(status_code=422, detail="Preferences belong to a person: use scope personal.")
    memory, created = await memory_service.add(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, scope=body.scope, kind=body.kind, content=body.content,
        created_by="agent", user_id=user_id, importance=body.importance, data=body.data, embedding=body.embedding,
        source={"type": "conversation" if actor.get("source") == "chat" else "run", "event_id": ws.event_id,
                "conversation_id": actor.get("conversation_id"), "document_ids": body.document_ids},
    )
    audit(ws, "MEMORY_SAVED" if created else "MEMORY_REINFORCED", "Memory", memory.id, {"scope": body.scope, "kind": body.kind})
    await ws.db.commit()
    return {**memory_service.memory_dict(memory), "created": created}


class RecallBody(BaseModel):
    query: str = ""
    embedding: Optional[List[float]] = None
    scopes: List[str] = Field(default_factory=lambda: ["workspace"])
    kinds: Optional[List[str]] = None
    limit: int = Field(8, ge=1, le=40)


@router.post("/memories/search")
async def recall(body: RecallBody, ws: Workspace = Depends(get_workspace)):
    actor = await _actor(ws)
    in_chat = actor.get("type") == "user" and actor.get("source") == "chat"
    found = await memory_service.search(
        ws.db, template_id=ws.template_id, query=body.query, embedding=body.embedding, scopes=body.scopes,
        user_id=actor.get("id") if in_chat else None, conversation_id=actor.get("conversation_id") if in_chat else None,
        kinds=body.kinds, limit=body.limit,
    )
    await ws.db.commit()
    return [memory_service.memory_dict(m, score) for m, score in found]


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(None, min_length=3, max_length=2000)
    importance: Optional[float] = Field(None, ge=0, le=1)
    archived: Optional[bool] = None
    data: Optional[Dict[str, Any]] = None
    embedding: Optional[List[float]] = None


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, body: MemoryPatch, ws: Workspace = Depends(get_workspace)):
    memory = await ws.db.get(Memory, memory_id)
    actor = await _actor(ws)
    if not memory or memory.template_id != ws.template_id or (memory.scope != "workspace" and memory.user_id != actor.get("id")):
        raise HTTPException(status_code=404, detail="Memory not found.")
    if body.content is not None and body.content != memory.content:
        memory.content, memory.needs_embedding = body.content.strip(), True
    for field in ("importance", "archived"):
        if getattr(body, field) is not None:
            setattr(memory, field, getattr(body, field))
    if body.data is not None:
        memory.data = {**(memory.data or {}), **body.data}
    await ws.db.flush()
    await memory_service.set_embedding(ws.db, memory.id, body.embedding)
    await ws.db.commit()
    return memory_service.memory_dict(memory)


@router.get("/memories/embedding-queue")
async def embedding_queue(limit: int = Query(20, ge=1, le=100), ws: Workspace = Depends(get_workspace)):
    """Memories whose text has no up-to-date embedding (empty when the database has no pgvector)."""
    return [{"id": m.id, "content": m.content} for m in await memory_service.needing_embeddings(ws.db, ws.template_id, limit)]


class EmbeddingBody(BaseModel):
    embedding: List[float]


@router.put("/memories/{memory_id}/embedding")
async def store_embedding(memory_id: str, body: EmbeddingBody, ws: Workspace = Depends(get_workspace)):
    memory = await ws.db.get(Memory, memory_id)
    if not memory or memory.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    stored = await memory_service.set_embedding(ws.db, memory.id, body.embedding)
    await ws.db.commit()
    return {"stored": stored}


class LegacyNote(BaseModel):
    kind: str = Field(..., pattern="^(lesson|observation)$")
    content: str
    data: Dict[str, Any] = Field(default_factory=dict)


class ImportBody(BaseModel):
    notes: List[LegacyNote]


@router.post("/memories/import")
async def import_notes(body: ImportBody, ws: Workspace = Depends(get_workspace)):
    """Moves the agent's older notes (kept in its own database before memory lived here)."""
    created = 0
    for note in body.notes:
        _, new = await memory_service.add(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, scope="workspace", kind=note.kind,
            content=note.content, created_by="agent", data=note.data, source={"type": "import"},
            importance=0.6 if note.kind == "lesson" else 0.4,
        )
        created += new
    await ws.db.commit()
    return {"imported": created}
