from __future__ import annotations

from typing import Any

from minibot.app.event_bus import EventBus
from minibot.core.events import AgentJobQueuedEvent
from minibot.core.jobs import AgentJob, AgentJobCreate, AgentJobRepository, AgentJobStatus
from minibot.shared.datetime_utils import utcnow


class AgentJobService:
    def __init__(self, repository: AgentJobRepository, event_bus: EventBus) -> None:
        self._repository = repository
        self._event_bus = event_bus

    async def create_job(self, payload: AgentJobCreate) -> AgentJob:
        job = await self._repository.create_job(payload)
        await self._event_bus.publish(
            AgentJobQueuedEvent(
                job_id=job.id,
                agent_name=job.agent_name,
                channel=job.channel,
                chat_id=job.chat_id,
                user_id=job.user_id,
            )
        )
        return job

    async def list_jobs(
        self,
        *,
        owner_id: str | None,
        channel: str | None,
        chat_id: int | None,
        user_id: int | None,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> list[AgentJob]:
        statuses = None
        if active_only:
            statuses = [AgentJobStatus.QUEUED, AgentJobStatus.LEASED, AgentJobStatus.RUNNING]
        jobs = await self._repository.list_jobs(
            owner_id=owner_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            statuses=statuses,
            limit=limit,
            offset=offset,
        )
        return list(jobs)

    async def request_cancel(self, *, job_id: str) -> dict[str, Any]:
        requested = await self._repository.request_cancel(job_id, requested_at=utcnow())
        job = await self._repository.get_job(job_id)
        if job is None:
            return {"ok": False, "job_id": job_id, "error_code": "job_not_found"}
        return {
            "ok": requested or job.status == AgentJobStatus.CANCELED,
            "job_id": job.id,
            "status": job.status.value,
            "cancel_requested": requested,
        }
