from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession


def resolve_sqlite_storage_path(sqlite_url: str) -> Path | None:
    url = make_url(sqlite_url)
    if url.database and url.drivername.startswith("sqlite") and url.database != ":memory":
        return Path(url.database)
    return None


def ensure_parent_dir(path: Path) -> None:
    directory = path.parent
    if directory and not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)


def like_pattern(value: str) -> str:
    """Wrap ``value`` in ``%`` for a ``LIKE ... ESCAPE '\\'`` substring match, escaping its wildcards."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def fts_match_query(query: str, joiner: str = "AND") -> str:
    """Turn free text into an FTS5 prefix query. Each token is quoted, so ``web:1`` or ``foo-bar``
    stay search terms instead of being parsed as column filters or operators. Tokens with no letter
    or digit (``-``, ``...``) are dropped: FTS5 indexes nothing for them, so under ``AND`` one would
    make the whole query match nothing."""
    tokens = [token.replace('"', "") for token in query.split()]
    separator = " OR " if joiner.upper() == "OR" else " AND "
    return separator.join(f'"{token}"*' for token in tokens if any(char.isalnum() for char in token))


async def lease_rows(
    session: AsyncSession,
    model: type[Any],
    *,
    now: datetime,
    limit: int,
    lease_deadline: datetime,
    order_by: Any,
    extra_where: Any | None = None,
) -> list[Any]:
    """Exclusively claim up to ``limit`` rows by flipping pending/expired-lease rows to ``leased``.

    ``model`` must expose ``id``, ``status``, ``lease_expires_at`` and ``updated_at`` columns and use
    the string status values ``pending`` and ``leased``. Candidates are over-fetched and then claimed
    one at a time with a conditional ``UPDATE``; the row is only handed back when that update reports
    a non-zero rowcount, which is what makes the claim exclusive against a concurrent leaser.

    Returns the refreshed ORM records; the caller owns the session and the commit.
    """

    def _claimable() -> Any:
        return or_(
            model.status == "pending",
            and_(
                model.status == "leased",
                or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now),
            ),
        )

    stmt = select(model).where(_claimable()).order_by(order_by).limit(limit * 4)
    if extra_where is not None:
        stmt = stmt.where(extra_where)

    candidates = list((await session.execute(stmt)).scalars().all())
    leased: list[Any] = []
    for record in candidates:
        if len(leased) >= limit:
            break
        claim = (
            update(model)
            .where(model.id == record.id)
            .where(_claimable())
            .values(status="leased", lease_expires_at=lease_deadline, updated_at=now)
        )
        outcome = await session.execute(claim.execution_options(synchronize_session=False))
        if getattr(outcome, "rowcount", 0):
            await session.refresh(record)
            leased.append(record)
    return leased
