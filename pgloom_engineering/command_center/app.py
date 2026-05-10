from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pgloom_engineering.command_center import store
from pgloom_engineering.command_center.auth import (
    DEV_ORIGINS,
    HostAllowlistMiddleware,
    LoopbackOnlyMiddleware,
    assert_allowed_websocket,
    assert_loopback_bind,
    assert_loopback_websocket,
)
from pgloom_engineering.command_center.realtime import (
    ListenNotifyBridge,
    WebSocketHub,
    websocket_loop,
)
from pgloom_engineering.command_center.static import SPAStaticFiles


class CommandCenterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PGLOOM_COMMAND_CENTER_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8765
    database_url: str | None = None
    start_realtime: bool = True
    local_only: bool = False
    allowed_hosts: str = ""
    dev_mode: bool = False

    @property
    def effective_database_url(self) -> str | None:
        return self.database_url or os.environ.get("PGLOOM_DATABASE_URL") or _env_value(
            "PGLOOM_DATABASE_URL"
        )

    @property
    def effective_dev_mode(self) -> bool:
        return self.dev_mode or os.environ.get("CC_DEV_MODE") == "1"

    @property
    def host_allowlist(self) -> set[str]:
        return _csv_values(self.allowed_hosts or os.environ.get("CC_ALLOWED_HOSTS", ""))


class InterventionIn(BaseModel):
    action_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None


def get_settings() -> CommandCenterSettings:
    return CommandCenterSettings()


def create_app(settings: CommandCenterSettings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.local_only:
        assert_loopback_bind(resolved.host)
    hub = WebSocketHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bridge: ListenNotifyBridge | None = None
        database_url = resolved.effective_database_url
        if resolved.start_realtime and database_url:
            bridge = ListenNotifyBridge(database_url, hub)
            await bridge.start()
        app.state.command_center_hub = hub
        try:
            yield
        finally:
            if bridge is not None:
                await bridge.stop()

    app = FastAPI(title="Command Center", lifespan=lifespan)
    app.add_middleware(HostAllowlistMiddleware, allowed_hosts=resolved.host_allowlist)
    if resolved.local_only:
        app.add_middleware(LoopbackOnlyMiddleware)
    if resolved.effective_dev_mode:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(DEV_ORIGINS),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
        )
    app.state.command_center_settings = resolved
    app.state.command_center_hub = hub

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "surface": "Command Center"}

    @app.get("/api/realtime/status")
    def realtime_status() -> dict[str, Any]:
        return {
            "channel": "cc_events",
            "subscribers": hub.subscriber_count,
            "max_queue_size": hub.max_queue_size,
            "start_realtime": resolved.start_realtime,
            "database_configured": bool(resolved.effective_database_url),
        }

    @app.get("/api/features")
    def api_features(
        limit: int = Query(default=50, ge=1, le=500)
    ) -> list[dict[str, Any]]:
        return store.list_features(database_url=resolved.effective_database_url, limit=limit)

    @app.get("/api/features/{feature_id}")
    def api_feature(
        feature_id: str
    ) -> dict[str, Any]:
        feature = store.get_feature(feature_id, database_url=resolved.effective_database_url)
        if feature is None:
            raise HTTPException(status_code=404, detail="feature not found")
        return feature

    @app.get("/api/features/{feature_id}/runs")
    def api_runs(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_runs(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/runs")
    def api_all_runs(
        limit: int = Query(default=500, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        return store.list_all_runs(database_url=resolved.effective_database_url, limit=limit)

    @app.get("/api/features/{feature_id}/runs/aggregate")
    def api_runs_aggregate(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.aggregate_runs(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/model-usage")
    def api_model_usage(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.model_usage(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/model-usage")
    def api_global_model_usage() -> list[dict[str, Any]]:
        return store.global_model_usage(database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/token-savior")
    def api_token_savior(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.token_savior(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/token-savior")
    def api_global_token_savior() -> list[dict[str, Any]]:
        return store.global_token_savior(database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/slots")
    def api_slots(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.slot_state(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/slots")
    def api_global_slots() -> list[dict[str, Any]]:
        return store.global_slot_state(database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/artifacts")
    def api_artifacts(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.artifacts(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/councils")
    def api_councils(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.councils(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/councils/{council_id}")
    def api_council_detail(
        feature_id: str,
        council_id: str,
    ) -> dict[str, Any]:
        council = store.council_detail(
            feature_id,
            council_id,
            database_url=resolved.effective_database_url,
        )
        if council is None:
            raise HTTPException(status_code=404, detail="council not found")
        return council

    @app.get("/api/features/{feature_id}/plans")
    def api_plans(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_plan_contracts",
            "created_at",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks")
    def api_tasks(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_task_contracts",
            "created_at, task_id",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks/{task_id}")
    def api_task_header(
        feature_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        task = store.task_header(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.get("/api/features/{feature_id}/tasks/{task_id}/runs")
    def api_task_runs(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_runs(feature_id, task_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/tasks/{task_id}/handoffs")
    def api_task_handoffs(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_handoffs(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks/{task_id}/qa")
    def api_task_qa(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_qa(feature_id, task_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/tasks/{task_id}/recovery")
    def api_task_recovery(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_recovery(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks/{task_id}/interventions")
    def api_task_interventions(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_interventions(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks/{task_id}/artifacts")
    def api_task_artifacts(
        feature_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        return store.task_artifacts(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/tasks/{task_id}/telemetry")
    def api_task_telemetry(
        feature_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        return store.task_telemetry(
            feature_id,
            task_id,
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/handoffs")
    def api_handoffs(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_handoffs",
            "created_at",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/recovery")
    def api_recovery(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_recovery_actions",
            "created_at",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/qa-signoffs")
    def api_qa_signoffs(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_qa_signoffs",
            "created_at",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/interventions")
    def api_interventions(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.list_table(
            feature_id,
            "engineering_operator_interventions",
            "created_at, id",
            database_url=resolved.effective_database_url,
        )

    @app.get("/api/features/{feature_id}/self-repair")
    def api_self_repair(
        feature_id: str
    ) -> list[dict[str, Any]]:
        return store.self_repair(feature_id, database_url=resolved.effective_database_url)

    @app.get("/api/features/{feature_id}/dag")
    def api_dag(
        feature_id: str
    ) -> dict[str, Any]:
        return store.dag(feature_id, database_url=resolved.effective_database_url)

    @app.post("/api/features/{feature_id}/interventions")
    def api_create_intervention(
        feature_id: str,
        body: InterventionIn
    ) -> dict[str, Any]:
        row = store.create_intervention(
            feature_id,
            action_type=body.action_type,
            payload=body.payload,
            actor=body.actor,
            database_url=resolved.effective_database_url,
        )
        if body.action_type in {"pause_feature", "resume_feature"}:
            hub.publish(
                {
                    "v": 1,
                    "kind": "feature.update",
                    "feature_id": feature_id,
                    "row_id": row.get("id"),
                    "fields": ["paused"],
                    "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    ),
                }
            )
        return row

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await assert_allowed_websocket(
            websocket,
            allowed_hosts=resolved.host_allowlist,
            dev_mode=resolved.effective_dev_mode,
        )
        if resolved.local_only:
            await assert_loopback_websocket(websocket)
        await websocket_loop(websocket, hub)

    dist = Path(__file__).with_name("web").joinpath("dist")
    if dist.exists():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="command-center-web")

    return app


def _env_value(key: str) -> str | None:
    path = Path(".env")
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name == key:
            return value.strip().strip('"').strip("'")
    return None


def _csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}
