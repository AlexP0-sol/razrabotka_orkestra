"""FastAPI HTTP + WebSocket operator server serving the offline dashboard (§13.16, §27.1)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

import orchestra
from orchestra.interfaces.ws_events import WebSocketHub, WSFrame

if TYPE_CHECKING:
    from orchestra.comms.escalation import EscalationManager
    from orchestra.comms.event_bus import EventBus
    from orchestra.inference.key_pool import KeyPoolManager
    from orchestra.skills.registry import SkillRegistry
    from orchestra.state.state_store import StateStore
    from orchestra.swarm.orchestrator import SwarmOrchestrator

WEB_STATIC_DIR = Path(__file__).resolve().parents[1] / "web"


class AddKeyRequest(BaseModel):
    """Request body for `POST /api/v1/keys`."""

    model_config = ConfigDict(extra="forbid")

    api_key: str


class AnswerQuestionRequest(BaseModel):
    """Request body for `POST /api/v1/questions/{question_id}/answer`."""

    model_config = ConfigDict(extra="forbid")

    answer: str


def create_operator_app(
    *,
    state_store: StateStore,
    event_bus: EventBus,
    key_pool: KeyPoolManager | None = None,
    skill_registry: SkillRegistry | None = None,
    escalation_manager: EscalationManager | None = None,
    orchestrator: SwarmOrchestrator | None = None,
    access_token: str | None = None,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    """Construct the FastAPI operator server and mount static offline web assets."""
    app = FastAPI(title="ORCHESTRA Operator Console", version=orchestra.__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ws_hub = WebSocketHub(
        state_store=state_store,
        event_bus=event_bus,
        key_pool=key_pool,
        skill_registry=skill_registry,
    )

    def _require_auth(
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
    ) -> None:
        if not access_token:
            return
        bearer = (
            authorization.removeprefix("Bearer ").strip()
            if authorization and authorization.startswith("Bearer ")
            else None
        )
        provided = bearer or token
        if provided != access_token:
            raise HTTPException(status_code=401, detail="Недействительный токен доступа")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        snap = state_store.snapshot()
        return {
            "status": "ok",
            "version": orchestra.__version__,
            "project_id": snap.project_id,
            "stage": snap.stage,
            "map_version": snap.version,
            "nodes_total": len(snap.nodes),
            "keys_total": key_pool.size if key_pool else 0,
        }

    @app.get("/api/v1/state", dependencies=[Depends(_require_auth)])
    async def get_state() -> dict[str, Any]:
        snap = state_store.snapshot()
        return {
            "mental_map": snap.model_dump(mode="json"),
            "mermaid": snap.export_mermaid(),
            "summary": snap.to_summary_dict(),
        }

    @app.get("/api/v1/nodes/{node_id}", dependencies=[Depends(_require_auth)])
    async def get_node(node_id: str) -> dict[str, Any]:
        snap = state_store.snapshot()
        if node_id not in snap.nodes:
            raise HTTPException(status_code=404, detail=f"Узел '{node_id}' не найден")
        return {
            "node": snap.nodes[node_id].model_dump(mode="json"),
            "submap": snap.project_submap(node_id).model_dump(mode="json"),
        }

    @app.get("/api/v1/keys", dependencies=[Depends(_require_auth)])
    async def list_keys() -> dict[str, Any]:
        stats = [s.model_dump(mode="json") for s in key_pool.snapshot_public()] if key_pool else []
        return {"keys": stats}

    @app.post("/api/v1/keys", dependencies=[Depends(_require_auth)])
    async def add_key(body: AddKeyRequest) -> dict[str, Any]:
        if key_pool is None:
            raise HTTPException(status_code=400, detail="KeyPool не инициализирован")
        kid = await key_pool.add_key(body.api_key)
        await event_bus.publish_topic("key_pool.updated", {"action": "added", "key_id": kid})
        return {"key_id": kid, "status": "ACTIVE"}

    @app.delete("/api/v1/keys/{key_id}", dependencies=[Depends(_require_auth)])
    async def remove_key(key_id: str) -> dict[str, Any]:
        if key_pool is None:
            raise HTTPException(status_code=400, detail="KeyPool не инициализирован")
        removed = await key_pool.remove_key(key_id, timeout_s=5.0)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Ключ '{key_id}' не найден")
        await event_bus.publish_topic("key_pool.updated", {"action": "removed", "key_id": key_id})
        return {"removed": True, "key_id": key_id}

    @app.post("/api/v1/keys/{key_id}/unquarantine", dependencies=[Depends(_require_auth)])
    async def unquarantine_key(key_id: str) -> dict[str, Any]:
        if key_pool is None:
            raise HTTPException(status_code=400, detail="KeyPool не инициализирован")
        ok = await key_pool.unquarantine(key_id)
        if not ok:
            raise HTTPException(status_code=400, detail="Невозможно разблокировать ключ")
        return {"unquarantined": True, "key_id": key_id}

    @app.get("/api/v1/skills", dependencies=[Depends(_require_auth)])
    async def list_skills() -> dict[str, Any]:
        if skill_registry is None:
            return {"skills": []}
        return {
            "skills": [s.model_dump(mode="json") for s in skill_registry.manifest.skills.values()]
        }

    @app.get("/api/v1/questions", dependencies=[Depends(_require_auth)])
    async def list_questions() -> dict[str, Any]:
        snap = state_store.snapshot()
        return {"questions": [q.model_dump(mode="json") for q in snap.open_questions.values()]}

    @app.post("/api/v1/questions/{question_id}/answer", dependencies=[Depends(_require_auth)])
    async def answer_question(question_id: str, body: AnswerQuestionRequest) -> dict[str, Any]:
        if escalation_manager is None:
            raise HTTPException(status_code=400, detail="EscalationManager не подключён")
        snap = state_store.snapshot()
        if question_id not in snap.open_questions:
            raise HTTPException(status_code=404, detail=f"Вопрос '{question_id}' не найден")
        resolved = await escalation_manager.answer_question(question_id, body.answer)
        return {"question": resolved.model_dump(mode="json")}

    @app.post("/api/v1/control/shutdown", dependencies=[Depends(_require_auth)])
    async def request_shutdown() -> dict[str, Any]:
        if orchestrator is not None:
            orchestrator.request_shutdown()
        return {"shutdown_requested": True}

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        since_seq: int = Query(default=0),
        token: str | None = Query(default=None),
    ) -> None:
        if access_token and token != access_token:
            await websocket.close(code=1008)
            return
        await ws_hub.connect(websocket, since_seq=since_seq)
        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    pong = WSFrame(type="pong", seq=since_seq)
                    await websocket.send_text(pong.model_dump_json())
        except WebSocketDisconnect:
            await ws_hub.disconnect(websocket)
        except Exception:
            await ws_hub.disconnect(websocket)

    if WEB_STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_STATIC_DIR)), name="static")

        @app.get("/")
        async def index_page() -> FileResponse:
            return FileResponse(WEB_STATIC_DIR / "index.html")

    return app
