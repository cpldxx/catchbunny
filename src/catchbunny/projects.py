"""Project bookkeeping shared by the CLI and the MCP server: one project per
app origin, created on first use and updated from what the caller passes."""

from __future__ import annotations

from .domain import Credential, Environment, LoginRecipe, Persona, Policy, Project, origin_of
from .engine.orchestrator import App


async def ensure_project(
    app: App,
    url: str,
    *,
    user: str = "",
    password: str = "",
    login_path: str = "/login",
    policy: Policy = Policy.SAFE_WRITES,
    knowledge: str = "",
    reset_path: str | None = None,
) -> Project:
    name = origin_of(url)
    existing = next((d for d in await app.store.list("project") if d["name"] == name), None)
    project = app.load_project(existing) if existing else Project(name=name, environments=[Environment(base_url=url)])
    project.environments[0].base_url = url
    project.policy = policy
    if knowledge:
        project.knowledge = knowledge
    if reset_path is not None:  # "" clears it; None leaves what the project already has
        project.environments[0].reset_path = reset_path
    if user:
        project.environments[0].login = LoginRecipe(path=login_path)
        member = Persona(name="member", role="Signed-in user", credential=Credential(username=user, password=password))
        project.personas = [p for p in project.personas if p.name != "member"] + [member]
    await app.save_project(project)
    return project
