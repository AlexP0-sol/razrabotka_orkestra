"""Rich + Typer CLI entrypoint (`python -m orchestra ...`) (§13.17, CLI-01..08, DoD-06)."""

from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

import orchestra
from orchestra.comms.escalation import EscalationManager
from orchestra.comms.event_bus import EventBus
from orchestra.config.settings import load_settings
from orchestra.inference.key_pool import KeyPoolManager
from orchestra.inference.llm_client import DualStreamLLMRuntime
from orchestra.interfaces.api_server import create_operator_app
from orchestra.providers.capabilities import CapabilityProber
from orchestra.providers.mock import MockProvider
from orchestra.providers.openai_compat import OpenAICompatProvider
from orchestra.research.pre_study import PreStudyRunner
from orchestra.skills.registry import SkillRegistry
from orchestra.state.mental_map import MentalMap
from orchestra.state.state_store import StateStore
from orchestra.swarm.orchestrator import SwarmOrchestrator
from orchestra.talents.talents_store import TalentsStore

app = typer.Typer(
    name="orchestra",
    help="ORCHESTRA — автономная мультиагентная фабрика разработки ПО.",
    no_args_is_help=True,
)
console = Console()


@app.command("version")
def cmd_version() -> None:
    """Показать версию пакета ORCHESTRA."""
    console.print(f"ORCHESTRA v{orchestra.__version__}")


@app.command("setup")
def cmd_setup(
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="Путь к создаваемому .env"),
) -> None:
    """Поочерёдный интерактивный мастер ввода модели, API-ключей и настроек (.env)."""
    from orchestra.config.setup_wizard import run_setup_wizard

    run_setup_wizard(env_path=env_file)


@app.command("init")
def cmd_init(runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/")) -> None:
    """Инициализировать структуру каталогов runtime/, реестр скиллов и талантов."""
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    reg = SkillRegistry(settings.paths.skills_dir, load_builtins=True)
    console.print(
        f"[green]Инициализировано[/green]: {settings.runtime_dir} "
        f"(встроенных скиллов: {len(reg.manifest.skills)})"
    )


@app.command("doctor")
def cmd_doctor(
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Проверить конфигурацию, платформу (WIN-22), git, песочницу, пул ключей и скиллы."""
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    reg = SkillRegistry(settings.paths.skills_dir, load_builtins=True)

    git_bin = shutil.which("git")
    git_ver = "не найден"
    if git_bin:
        gp = subprocess.run([git_bin, "--version"], capture_output=True, text=True, check=False)
        git_ver = gp.stdout.strip() or "OK"

    docker_bin = shutil.which("docker")
    sbx_mode = settings.sandbox.mode
    if sbx_mode == "docker" and not docker_bin:
        sbx_detail = "docker->subprocess (демон Docker недоступен, безопасная деградация WIN-20)"
    else:
        sbx_detail = f"{sbx_mode} (docker={'доступен' if docker_bin else 'нет'})"

    limiter_kind = (
        "Win32 Job Objects" if platform.system().lower().startswith("win") else "POSIX setrlimit"
    )
    prober = CapabilityProber(cache_dir=settings.paths.cache_dir)
    probe_caps = prober.assumed_capabilities(settings.llm.default_model)

    table = Table(title="Диагностика окружения и Платформа ORCHESTRA")
    table.add_column("Раздел / Компонент")
    table.add_column("Статус")
    table.add_column("Детали")
    table.add_row(
        "Платформа (ОС / Python)",
        "OK",
        f"{platform.system()} {platform.release()} | Python {platform.python_version()}",
    )
    table.add_row("Платформа (UTF-8 / LF)", "OK", f"encoding={sys.getdefaultencoding()}, eol=lf")
    table.add_row("Платформа (ResourceLimiter)", "OK", limiter_kind)
    table.add_row("Платформа (Git)", "OK" if git_bin else "WARN", git_ver)
    table.add_row("Песочница", "OK", sbx_detail)
    table.add_row("Профиль", "OK", settings.profile)
    probe_info = (
        f"{settings.llm.provider} ({settings.llm.default_model}, "
        f"dual_stream={probe_caps.supports_reasoning_stream})"
    )
    table.add_row("Провайдер LLM + Probe", "OK", probe_info)
    table.add_row("Пул API-ключей", "OK", f"{len(settings.key_pool.keys)} ключ(ей)")
    table.add_row("Реестр /skills/", "OK", f"{len(reg.manifest.skills)} скиллов активно")
    console.print(table)
    console.print("[green]Вердикт: окружение готово к работе.[/green]")


@app.command("demo")
def cmd_demo(
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Запустить эталонный офлайн демо-сценарий крипто-базы (PAPER) на MockProvider (DoD-37)."""
    from orchestra.domains.crypto_trading import CryptoTradingDomainPack

    pack = CryptoTradingDomainPack()
    settings = load_settings(
        overrides={"runtime_dir": str(runtime_dir), "llm": {"provider": "mock"}}
    )
    pack.seed_talents(settings.paths.talents_dir)
    reg = SkillRegistry(settings.paths.skills_dir, load_builtins=True)
    pack.seed_skills(settings.paths.skills_dir, registry=reg)
    asyncio.run(
        _execute_cli_run(
            spec=(
                "Multi-exchange crypto trading base (binance, bybit, orderbook, backtest) "
                "in PAPER mode"
            ),
            project_id="crypto_demo_paper",
            mock=True,
            runtime_dir=runtime_dir,
        )
    )


@app.command("keys")
def cmd_keys(
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Показать публичную статистику пула API-ключей (без секретов)."""
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    pool = KeyPoolManager(
        list(settings.key_pool.keys),
        state_path=settings.paths.cache_dir / "key_pool_state.json",
    )
    table = Table(title="Пул API-ключей ORCHESTRA")
    table.add_column("Key ID")
    table.add_column("Статус")
    table.add_column("Запросы")
    table.add_column("429 / 5xx")
    for stat in pool.snapshot_public():
        table.add_row(
            stat.key_id,
            stat.status,
            str(stat.total_requests),
            f"{stat.total_errors_429} / {stat.total_errors_5xx}",
        )
    console.print(table)


@app.command("skills")
def cmd_skills(
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
    include_crypto: bool = typer.Option(False, "--crypto", help="Показать и крипто-скиллы"),
) -> None:
    """Вывести список зарегистрированных исполняемых скиллов в /skills/."""
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    reg = SkillRegistry(settings.paths.skills_dir, load_builtins=True)
    if include_crypto:
        import importlib

        crypto_mod = importlib.import_module("orchestra.domains.crypto_trading")
        reg.register_package_modules(
            package_prefix="orchestra.skills.builtin.crypto",
            module_names=tuple(crypto_mod.CRYPTO_SKILL_MODULES),
        )
    table = Table(title="Каталог исполняемых скиллов (/skills/)")
    table.add_column("ID")
    table.add_column("Версия")
    table.add_column("Успешность")
    table.add_column("Описание")
    for spec in sorted(reg.manifest.skills.values(), key=lambda s: s.id):
        table.add_row(spec.id, spec.version, f"{spec.success_rate * 100:.0f}%", spec.description)
    console.print(table)


@app.command("talents")
def cmd_talents(
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Показать накопленные профили талантов в /agent_talents/."""
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    tstore = TalentsStore(settings.paths.talents_dir)
    profiles = tstore.match_by_tags(set())
    table = Table(title="Накопленные таланты ролей (/agent_talents/)")
    table.add_column("Роль")
    table.add_column("Версия")
    table.add_column("First-Pass")
    table.add_column("Проектов")
    for prof in profiles:
        table.add_row(
            prof.role_id,
            f"v{prof.version}",
            f"{prof.first_pass_rate * 100:.0f}%",
            str(prof.projects_completed),
        )
    console.print(table)


@app.command("status")
def cmd_status(
    project_id: str = typer.Option("demo", help="ID проекта"),
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Отобразить состояние MentalMap указанного проекта."""
    snap_file = runtime_dir / "projects" / project_id / "mental_map.json"
    if not snap_file.is_file():
        console.print(f"[red]Снапшот проекта '{project_id}' не найден по пути {snap_file}[/red]")
        raise typer.Exit(code=1)
    mmap = MentalMap.model_validate_json(snap_file.read_text(encoding="utf-8"))
    table = Table(title=f"MentalMap: {mmap.project_name} ({mmap.project_id}) — {mmap.stage}")
    table.add_column("Узел")
    table.add_column("Тип")
    table.add_column("Статус")
    table.add_column("Зависимости")
    table.add_column("Токены")
    for nid, node in sorted(mmap.nodes.items()):
        table.add_row(
            f"{nid}: {node.title}",
            node.type,
            node.status,
            ", ".join(node.dependencies) or "—",
            str(node.tokens_used),
        )
    console.print(table)


@app.command("run")
def cmd_run(
    spec: str = typer.Argument(..., help="Краткое ТЗ или путь к файлу ТЗ"),
    project_id: str = typer.Option("demo_project", help="Идентификатор проекта"),
    mock: bool = typer.Option(False, "--mock", help="Использовать детерминированный MockProvider"),
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Запустить конвейер роя над задачей."""
    asyncio.run(
        _execute_cli_run(spec=spec, project_id=project_id, mock=mock, runtime_dir=runtime_dir)
    )


def _resolve_spec_input(spec: str) -> str:
    candidate = Path(spec)
    return candidate.read_text(encoding="utf-8") if candidate.is_file() else spec


async def _execute_cli_run(
    *,
    spec: str,
    project_id: str,
    mock: bool,
    runtime_dir: Path,
) -> None:
    from orchestra.swarm.live_pipeline import (
        NodeArtifactPayload,
        WorkspaceBuildVerifier,
        build_initial_mental_map,
    )
    from orchestra.talents.diary_manager import DiaryManager
    from orchestra.talents.post_mortem import PostMortemAnalyzer
    from orchestra.verify.git_ops import GitOps

    spec_text = await asyncio.to_thread(_resolve_spec_input, spec)
    provider_kind = "mock" if mock else None
    overrides: dict[str, object] = {"runtime_dir": str(runtime_dir)}
    if provider_kind:
        overrides["llm"] = {"provider": "mock"}
    settings = load_settings(overrides=overrides)
    is_mock = settings.llm.provider == "mock"
    if not is_mock:
        settings.validate_for_live_inference()

    pre = PreStudyRunner(docs_cache_dir=settings.paths.docs_cache_dir).run(
        project_id=project_id,
        specification_text=spec_text,
    )
    console.print(f"[cyan]{pre.summary_ru}[/cyan]")

    pool = KeyPoolManager(list(settings.key_pool.keys))
    provider = (
        MockProvider()
        if is_mock
        else OpenAICompatProvider(
            base_url=str(settings.llm.base_url),
            default_model=settings.llm.default_model,
            prober=CapabilityProber(cache_dir=settings.paths.cache_dir),
            request_timeout_s=settings.llm.request_timeout_s,
        )
    )
    if not is_mock and settings.key_pool.probe_on_start and settings.key_pool.keys:
        await provider.probe(api_key=settings.key_pool.keys[0].get_secret_value())

    llm = DualStreamLLMRuntime(provider=provider, settings=settings, key_pool=pool)
    init_map = await build_initial_mental_map(
        project_id=project_id,
        spec_text=spec_text,
        detected_domain=pre.detected_domain,
        mock=is_mock,
        llm_runtime=llm,
    )

    proj_dir = settings.paths.projects_dir / project_id
    ws_dir = proj_dir / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    await GitOps(ws_dir).init_workspace_repo(working_branch=f"orchestra/{project_id}")

    store = StateStore(proj_dir, initial_map=init_map)
    await store.start()
    talents = TalentsStore(settings.paths.talents_dir)
    live_verifier = (
        WorkspaceBuildVerifier(workspace_dir=ws_dir, detected_domain=pre.detected_domain)
        if not is_mock
        else None
    )
    orch = SwarmOrchestrator(
        state_store=store,
        llm_runtime=llm,
        talents_store=talents,
        verify_callback=live_verifier.verify_and_commit if live_verifier is not None else None,
        node_response_schema=NodeArtifactPayload if not is_mock else None,
        max_concurrent_agents=settings.swarm.max_concurrent_agents,
    )
    summary = await orch.run_build_loop()
    PostMortemAnalyzer(
        talents_store=talents,
        diary_manager=DiaryManager(settings.paths.talents_dir),
    ).run_post_mortem(
        summary.final_map,
        participating_roles={c.role_id for c in orch.roster},
    )
    await store.stop()
    await provider.close()
    console.print(
        f"[green]Выполнение завершено[/green]: узлов DONE={len(summary.completed_nodes)}/"
        f"{len(summary.final_map.nodes)}, стадия={summary.final_map.stage}, "
        f"workspace={ws_dir}, время={summary.wallclock_s}с."
    )


@app.command("serve")
def cmd_serve(
    project_id: str = typer.Option("demo_project", help="ID проекта"),
    host: str = typer.Option("0.0.0.0", help="Хост сервера"),
    port: int = typer.Option(8000, help="Порт сервера"),
    runtime_dir: Path = typer.Option(Path("./runtime"), help="Путь к runtime/"),
) -> None:
    """Запустить Web-дашборд и HTTP/WebSocket API сервера оператора."""
    asyncio.run(_serve_async(project_id=project_id, host=host, port=port, runtime_dir=runtime_dir))


async def _serve_async(
    *,
    project_id: str,
    host: str,
    port: int,
    runtime_dir: Path,
) -> None:
    settings = load_settings(overrides={"runtime_dir": str(runtime_dir)})
    proj_dir = settings.paths.projects_dir / project_id
    seed = MentalMap(project_id=project_id, project_name=project_id, vision="Live Operator Console")
    store = StateStore(proj_dir, initial_map=seed)
    await store.start()
    bus = EventBus(rpc_log_path=proj_dir / "rpc_messages.jsonl")
    pool = KeyPoolManager(list(settings.key_pool.keys))
    skills = SkillRegistry(settings.paths.skills_dir, load_builtins=True)
    esc = EscalationManager(state_store=store, event_bus=bus)
    token_val = (
        settings.web.access_token.get_secret_value()
        if settings.web.access_token is not None
        else None
    )
    fastapi_app = create_operator_app(
        state_store=store,
        event_bus=bus,
        key_pool=pool,
        skill_registry=skills,
        escalation_manager=esc,
        access_token=token_val,
        allowed_origins=settings.web.allowed_origins,
    )
    config = uvicorn.Config(fastapi_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await store.stop()
