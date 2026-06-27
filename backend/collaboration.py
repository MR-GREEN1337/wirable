# File: backend/src/api/v1/endpoints/collaboration.py
"""Live canvas collaboration — cursor sharing via Redis pub/sub + SSE."""
import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.security import get_current_active_user_or_api_key_user
from src.core.sse_utils import with_heartbeat
from src.db.models import Agent, User
from src.db.postgresql import get_readonly_session, get_session
from src.db.redis import get_redis_pool

router = APIRouter()


class CursorPayload(BaseModel):
    x: float
    y: float


class GraphSyncPayload(BaseModel):
    """A graph change delta broadcast to all peers."""

    nodes: list[dict] | None = None
    edges: list[dict] | None = None
    action: str = "update"  # update | add_node | remove_node | add_edge | remove_edge


def _channel(agent_id: uuid.UUID) -> str:
    return f"canvas:collab:{agent_id}"


async def _check_access(
    agent_id: uuid.UUID, current_user: User, session: AsyncSession
) -> Agent:
    agent = await session.get(Agent, agent_id)
    if not agent or agent.organization_id != current_user.active_organization_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("/{agent_id}/collab/cursor", status_code=204)
async def publish_cursor(
    agent_id: uuid.UUID,
    body: CursorPayload,
    current_user: User = Depends(get_current_active_user_or_api_key_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis_pool),
):
    """Broadcast the caller's cursor position to all peers on this canvas."""
    await _check_access(agent_id, current_user, session)
    payload = json.dumps(
        {
            "type": "cursor",
            "user_id": str(current_user.id),
            "user_name": current_user.full_name or current_user.email.split("@")[0],
            "avatar_url": current_user.avatar_url or None,
            "x": body.x,
            "y": body.y,
            "ts": datetime.utcnow().isoformat(),
        }
    )
    await redis.publish(_channel(agent_id), payload)


@router.post("/{agent_id}/collab/graph", status_code=204)
async def publish_graph_sync(
    agent_id: uuid.UUID,
    body: GraphSyncPayload,
    current_user: User = Depends(get_current_active_user_or_api_key_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis_pool),
):
    """Broadcast a graph change delta to all peers editing this agent."""
    await _check_access(agent_id, current_user, session)
    payload = json.dumps(
        {
            "type": "graph_sync",
            "user_id": str(current_user.id),
            "user_name": current_user.full_name or current_user.email.split("@")[0],
            "action": body.action,
            "nodes": body.nodes,
            "edges": body.edges,
            "ts": datetime.utcnow().isoformat(),
        }
    )
    await redis.publish(_channel(agent_id), payload)


@router.get("/{agent_id}/collab/stream")
async def collab_stream(
    agent_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_active_user_or_api_key_user),
    session: AsyncSession = Depends(get_readonly_session),
    redis: Redis = Depends(get_redis_pool),
):
    """SSE stream of peer cursor events for a canvas collaboration session."""
    await _check_access(agent_id, current_user, session)
    channel = _channel(agent_id)

    # Announce arrival to peers already on the canvas
    await redis.publish(
        channel,
        json.dumps(
            {
                "type": "join",
                "user_id": str(current_user.id),
                "user_name": current_user.full_name or current_user.email.split("@")[0],
                "avatar_url": current_user.avatar_url or None,
                "ts": datetime.utcnow().isoformat(),
            }
        ),
    )

    async def event_gen():
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg:
                    raw = msg["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    # Skip own events — client already knows its own position
                    try:
                        if json.loads(raw).get("user_id") == str(current_user.id):
                            continue
                    except Exception:  # nosec
                        pass
                    yield f"data: {raw}\n\n"
                await asyncio.sleep(0.01)
        finally:
            # Tell peers this user left
            try:
                await redis.publish(
                    channel,
                    json.dumps(
                        {
                            "type": "leave",
                            "user_id": str(current_user.id),
                            "ts": datetime.utcnow().isoformat(),
                        }
                    ),
                )
            except Exception:  # nosec
                pass
            await pubsub.unsubscribe(channel)

    return StreamingResponse(
        with_heartbeat(event_gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
