import os
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from pydantic import BaseModel, Field, HttpUrl


class BrowserTask(BaseModel):
    project_id: UUID
    task_id: UUID
    todo_id: UUID
    instruction: str = Field(min_length=1, max_length=10_000)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    start_url: HttpUrl | None = None
    max_steps: int = Field(default=10, ge=1, le=30)


class AgentResult(BaseModel):
    status: str
    result_summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


app = FastAPI(title="Surake Browser Agent", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "agent": "browser"}


@app.post("/v1/tasks", response_model=AgentResult)
async def run_task(task: BrowserTask) -> AgentResult:
    if not os.getenv("OPENROUTER_API_KEY"):
        return AgentResult(
            status="blocked",
            result_summary="Browser task requires OPENROUTER_API_KEY.",
            blockers=["OPENROUTER_API_KEY is not configured"],
        )

    from browser_use import Agent, Browser, ChatOpenRouter

    instruction = task.instruction
    if task.start_url:
        instruction = f"Open {task.start_url} first. {instruction}"
    if task.allowed_domains:
        domains = ", ".join(task.allowed_domains)
        instruction += f" Only visit these domains: {domains}."

    browser = Browser(allowed_domains=task.allowed_domains or None)
    try:
        agent = Agent(
            task=instruction,
            llm=ChatOpenRouter(model=os.getenv("BROWSER_AGENT_MODEL", "openai/gpt-4.1-mini")),
            browser=browser,
        )
        history = await agent.run(max_steps=task.max_steps)
        final_result = history.final_result() or "Browser task ended without a final result."
        urls = list(dict.fromkeys(history.urls())) if hasattr(history, "urls") else []
        return AgentResult(
            status="completed" if final_result else "failed",
            result_summary=final_result,
            evidence=[{"type": "url", "value": url} for url in urls],
        )
    except Exception as exc:
        return AgentResult(
            status="failed",
            result_summary="Browser task failed.",
            blockers=[f"{type(exc).__name__}: {exc}"],
        )
    finally:
        await browser.close()


def main() -> None:
    import uvicorn

    uvicorn.run("browser_agent.main:app", host="127.0.0.1", port=8101)
