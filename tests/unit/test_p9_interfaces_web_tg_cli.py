"""Unit tests for Phase P9: FastAPI + WebSocket operator console, offline Web UI, TG, and CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orchestra.comms.escalation import EscalationManager, EscalationRequest
from orchestra.comms.event_bus import EventBus
from orchestra.config.constants import TG_CALLBACK_DATA_LIMIT
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.interfaces.api_server import WEB_STATIC_DIR, create_operator_app
from orchestra.interfaces.cli import app as cli_app
from orchestra.interfaces.telegram_bridge import (
    TelegramBridge,
    encode_callback_data,
    split_telegram_message,
)
from orchestra.skills.registry import SkillRegistry
from orchestra.state.mental_map import MapNode, MentalMap
from orchestra.state.state_store import StateStore

if TYPE_CHECKING:
    from pathlib import Path


def test_offline_web_assets_contain_zero_external_cdn_links() -> None:
    """Verify ADR-0007 / WS-07: static web dashboard contains zero external http(s) CDN links."""
    assert (WEB_STATIC_DIR / "index.html").is_file()
    assert (WEB_STATIC_DIR / "styles.css").is_file()
    assert (WEB_STATIC_DIR / "app.js").is_file()

    for asset in ("index.html", "styles.css", "app.js"):
        content = (WEB_STATIC_DIR / asset).read_text(encoding="utf-8")
        # SVG XML namespace http://www.w3.org/2000/svg is a W3C namespace URI, not a network fetch
        cleaned = content.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in cleaned
        assert "http://" not in cleaned
        assert "localhost" not in cleaned
        assert "127.0.0.1" not in cleaned


async def test_fastapi_endpoints_auth_key_management_and_websocket(tmp_path: Path) -> None:
    """Verify REST endpoints, secret-free key list, question answering, and WS snapshot/ping."""
    init_map = MentalMap(
        project_id="proj_ui",
        project_name="UI Test",
        vision="Operator UI test",
        nodes={"N1": MapNode(id="N1", title="Core", status="READY")},
    )
    store = StateStore(tmp_path / "proj", initial_map=init_map)
    await store.start()
    bus = EventBus()
    raw_secret = "nvapi-" + "super-secret-ui-key-9999"
    pool = KeyPoolManager([raw_secret])
    skills = SkillRegistry(tmp_path / "skills", load_builtins=True)
    esc = EscalationManager(state_store=store, event_bus=bus)
    esc_res = await esc.escalate(
        EscalationRequest(
            author_agent="lead_architect_01",
            node_id="N1",
            is_business_decision=True,
            question_ru="Какой режим запустить?",
            options_ru=("PAPER", "SHADOW"),
        )
    )
    assert esc_res.question_id is not None

    fastapi_app = create_operator_app(
        state_store=store,
        event_bus=bus,
        key_pool=pool,
        skill_registry=skills,
        escalation_manager=esc,
        access_token="op-token-123",
    )

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/")).status_code == 200
        # Unauthenticated API call rejected with 401
        assert (await client.get("/api/v1/state")).status_code == 401

        headers = {"Authorization": "Bearer op-token-123"}
        state_resp = await client.get("/api/v1/state", headers=headers)
        assert state_resp.status_code == 200
        assert "flowchart TD" in state_resp.json()["mermaid"]

        keys_resp = await client.get("/api/v1/keys", headers=headers)
        assert keys_resp.status_code == 200
        assert raw_secret not in keys_resp.text

        add_resp = await client.post(
            "/api/v1/keys",
            headers=headers,
            json={"api_key": "nvapi-added-via-api-0002"},
        )
        assert add_resp.status_code == 200
        added_id = add_resp.json()["key_id"]
        assert (await client.delete(f"/api/v1/keys/{added_id}", headers=headers)).status_code == 200

        ans_resp = await client.post(
            f"/api/v1/questions/{esc_res.question_id}/answer",
            headers=headers,
            json={"answer": "PAPER"},
        )
        assert ans_resp.status_code == 200

    await store.stop()


def test_websocket_snapshot_init_and_ping_pong(tmp_path: Path) -> None:
    """Verify WebSocket `/ws` emits `snapshot.init` and answers `ping` with `pong`."""
    import asyncio

    init_map = MentalMap(project_id="proj_ws", project_name="WS", vision="WS Test")
    store = StateStore(tmp_path / "ws_proj", initial_map=init_map)
    asyncio.run(store.start())
    asyncio.run(store.stop())
    bus = EventBus()
    ws_app = create_operator_app(state_store=store, event_bus=bus, access_token="ws-tok")

    with TestClient(ws_app) as client, client.websocket_connect("/ws?token=ws-tok") as ws:
        init_frame = ws.receive_json()
        assert init_frame["type"] == "snapshot.init"
        ws.send_json({"type": "ping"})
        pong_frame = ws.receive_json()
        assert pong_frame["type"] == "pong"


class _FakeTelegramTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, list[tuple[str, str]] | None]] = []
        self.edits: list[tuple[int, int, str]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        buttons: list[tuple[str, str]] | None = None,
    ) -> int:
        self.sent.append((chat_id, text, buttons))
        return len(self.sent)

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append((chat_id, message_id, text))


async def test_telegram_bridge_allowlist_splitting_throttling_and_callbacks(
    tmp_path: Path,
) -> None:
    """Verify TG-02 allowlist, TG-05 <=64B callback_data, TG-06 4096 split, and TG-07 throttle."""
    store = StateStore(
        tmp_path / "tg_proj",
        initial_map=MentalMap(
            project_id="p_tg",
            project_name="TG",
            nodes={"N1": MapNode(id="N1", title="Core", status="READY")},
        ),
    )
    await store.start()
    esc = EscalationManager(state_store=store)
    transport = _FakeTelegramTransport()
    bridge = TelegramBridge(
        allowed_user_ids={777},
        transport=transport,
        state_store=store,
        escalation_manager=esc,
        edit_throttle_s=60.0,
    )

    # Unauthorized user silently ignored & logged
    unauth = await bridge.handle_command(user_id=999, chat_id=999, text="/status")
    assert unauth is None
    assert bridge.unauthorized_attempts == [999]

    # Authorized Russian /status
    auth_reply = await bridge.handle_command(user_id=777, chat_id=777, text="/status")
    assert auth_reply is not None and "Статус проекта" in auth_reply

    # Message splitter <= 4096 chars
    parts = split_telegram_message("А" * 9000)
    assert len(parts) == 3
    assert all(len(p) <= 4096 for p in parts)

    # Callback data <= 64 bytes even for long question IDs
    cb = encode_callback_data("Q_" + "x" * 90, 2)
    assert len(cb.encode("utf-8")) <= TG_CALLBACK_DATA_LIMIT

    # Throttling blocks rapid consecutive edits
    assert await bridge.throttled_edit_status(777, 1, "Обновление 1") is True
    assert await bridge.throttled_edit_status(777, 1, "Обновление 2") is False

    # Escalation button callback resolves question and unblocks node
    res = await esc.escalate(
        EscalationRequest(
            author_agent="lead_architect_01",
            node_id="N1",
            is_business_decision=True,
            question_ru="Выбрать БД?",
            options_ru=("SQLite", "PostgreSQL"),
        )
    )
    assert res.question_id is not None
    q_obj = store.snapshot().open_questions[res.question_id]
    await bridge.send_question_card(777, q_obj)
    cb_str = encode_callback_data(q_obj.id, 0)
    cb_out = await bridge.handle_callback(user_id=777, chat_id=777, data=cb_str)
    assert cb_out is not None and cb_out["answer"] == "SQLite"
    assert store.snapshot().nodes["N1"].status == "READY"
    await store.stop()


def test_cli_commands_init_doctor_skills_and_mock_run(tmp_path: Path) -> None:
    """Verify CLI subcommands (`--help`, `init`, `doctor`, `skills`, `run --mock`, `status`)."""
    runner = CliRunner()
    rt = tmp_path / "cli_rt"

    assert runner.invoke(cli_app, ["--help"]).exit_code == 0
    assert runner.invoke(cli_app, ["init", "--runtime-dir", str(rt)]).exit_code == 0
    assert runner.invoke(cli_app, ["doctor", "--runtime-dir", str(rt)]).exit_code == 0
    assert runner.invoke(cli_app, ["skills", "--runtime-dir", str(rt)]).exit_code == 0
    run_res = runner.invoke(
        cli_app,
        [
            "run",
            "Create deterministic calculator utility",
            "--project-id",
            "calc_demo",
            "--mock",
            "--runtime-dir",
            str(rt),
        ],
    )
    assert run_res.exit_code == 0, run_res.output
    status_res = runner.invoke(
        cli_app,
        ["status", "--project-id", "calc_demo", "--runtime-dir", str(rt)],
    )
    assert status_res.exit_code == 0
    assert "DONE" in status_res.output
