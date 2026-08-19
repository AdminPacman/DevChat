import re
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from server.settings import WARE_HOUSE_DIR
from utils.exceptions import ResourceNotFoundError, ValidationError
from utils.structured_logger import get_server_logger, LogType

router = APIRouter()


@router.get("/api/sessions/{session_id}/download")
async def download_session(session_id: str):
    try:
        if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
            logger = get_server_logger()
            logger.log_security_event(
                "INVALID_SESSION_ID_FORMAT",
                f"Invalid session_id format: {session_id}",
                details={"received_session_id": session_id},
            )
            raise ValidationError(
                "Invalid session_id: only letters, digits, underscores, and hyphens are allowed",
                field="session_id",
            )

        dir_name = f"session_{session_id}"
        session_path = WARE_HOUSE_DIR / dir_name

        if not session_path.exists() or not session_path.is_dir():
            raise ResourceNotFoundError(
                "Session directory not found",
                resource_type="session",
                resource_id=session_id,
            )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            zip_path = Path(tmp_file.name)

        archive_base = zip_path.with_suffix("")
        try:
            shutil.make_archive(str(archive_base), "zip", root_dir=WARE_HOUSE_DIR, base_dir=dir_name)
        except Exception as exc:
            if zip_path.exists():
                zip_path.unlink()
            logger = get_server_logger()
            logger.log_exception(exc, f"Failed to create zip archive for session: {session_id}")
            raise HTTPException(status_code=500, detail="Failed to create zip archive")

        logger = get_server_logger()
        logger.info(
            "Session download prepared",
            log_type=LogType.WORKFLOW,
            session_id=session_id,
            archive_path=str(zip_path),
        )

        def cleanup_zip():
            if zip_path.exists():
                zip_path.unlink()

        return FileResponse(
            path=zip_path,
            filename=f"{dir_name}.zip",
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={dir_name}.zip"},
            background=BackgroundTask(cleanup_zip),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Session directory not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger = get_server_logger()
        logger.log_exception(exc, f"Unexpected error during session download: {session_id}")
        raise HTTPException(status_code=500, detail="Failed to download session")


# How much of the tail to hand back. Same instinct as the caps in
# functions/function_calling/_fleet_common.py: never return a raw stream whole.
# This is a UI peephole, not a log download — the full stream is already on disk
# and the whole session is downloadable via the route above.
_TAIL_MAX_BYTES = 8000
_TAIL_DEFAULT_BYTES = 4000

# Label is a PATH COMPONENT. Anything outside this charset is rejected rather than
# sanitised, so "../../etc/passwd" can never be normalised into something readable.
_LABEL_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@router.get("/api/sessions/{session_id}/agent-tail")
async def get_agent_tail(session_id: str, label: str, limit: int = _TAIL_DEFAULT_BYTES):
    """Tail a fleet agent's live log while it is still running.

    WHY THIS EXISTS. `run_agent` streams a child's stdout+stderr straight to
    `<workspace>/_agent_logs/<label>.live.log` as it is produced, but nothing ever
    read it. Between a tool call's start and its finish the WebSocket emits nothing
    at all — on a real measured run that was a 21.6-minute silence, during which the
    console showed a ticking timer that is a plain Date.now() diff and carries no
    information about the subprocess it appears to be timing.

    So the file was already the only live signal in the system, and this simply
    surfaces it. Deterministic file read, no model involved.

    Honest about what it cannot tell you: `stale_seconds` is how long since the log
    last grew. That is a HEURISTIC, not liveness. A quiet log can mean "hung" or
    "thinking hard before writing" and this endpoint cannot distinguish them —
    callers must present it as a hint, never as "the agent is stuck".
    """
    logger = get_server_logger()

    if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
        logger.log_security_event(
            "INVALID_SESSION_ID_FORMAT",
            f"Invalid session_id format: {session_id}",
            details={"received_session_id": session_id},
        )
        raise HTTPException(status_code=400, detail="Invalid session_id")

    if not _LABEL_RE.match(label or ""):
        logger.log_security_event(
            "INVALID_AGENT_LABEL",
            f"Invalid agent label: {label}",
            details={"received_label": label},
        )
        raise HTTPException(status_code=400, detail="Invalid label")

    limit = max(1, min(int(limit), _TAIL_MAX_BYTES))

    log_path = (
        WARE_HOUSE_DIR
        / f"session_{session_id}"
        / "code_workspace"
        / "_agent_logs"
        / f"{label}.live.log"
    )

    # A missing log is NORMAL — the agent may not have written its first byte yet.
    # 200 with running=false keeps "hasn't started" distinguishable from "lookup failed".
    if not log_path.is_file():
        return {
            "session_id": session_id,
            "label": label,
            "exists": False,
            "tail": "",
            "size": 0,
            "stale_seconds": None,
        }

    try:
        stat = log_path.stat()
        with log_path.open("rb") as handle:
            if stat.st_size > limit:
                handle.seek(stat.st_size - limit)
            chunk = handle.read()
        # The seek can land mid-codepoint; drop the partial rather than raising.
        tail = chunk.decode("utf-8", errors="replace")
        if stat.st_size > limit:
            tail = tail.split("\n", 1)[-1]
    except OSError as exc:
        logger.error(
            "Failed to read agent live log",
            log_type=LogType.ERROR,
            session_id=session_id,
            label=label,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to read agent log")

    return {
        "session_id": session_id,
        "label": label,
        "exists": True,
        "tail": tail,
        "size": stat.st_size,
        "truncated": stat.st_size > limit,
        "stale_seconds": max(0.0, time.time() - stat.st_mtime),
    }
