from fastapi import APIRouter, HTTPException

from server.models import VueGraphContentPayload, VueGraphLayoutPayload
from server.services.vuegraphs_storage import (
    fetch_vuegraph_content,
    fetch_vuegraph_layout,
    save_vuegraph_content,
    save_vuegraph_layout,
)
from utils.structured_logger import get_server_logger, LogType

router = APIRouter()


@router.post("/api/vuegraphs/upload/content")
async def upload_vuegraph_content(payload: VueGraphContentPayload):
    logger = get_server_logger()
    try:
        save_vuegraph_content(payload.filename, payload.content)
    except Exception as exc:
        logger.error(
            "Failed to persist Vue graph content",
            log_type=LogType.ERROR,
            filename=payload.filename,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to save graph content")

    logger.info(
        "Vue graph content saved",
        log_type=LogType.REQUEST,
        filename=payload.filename,
    )
    return {"filename": payload.filename, "status": "saved"}


@router.post("/api/vuegraphs/upload/layout")
async def upload_vuegraph_layout(payload: VueGraphLayoutPayload):
    """Persist a canvas layout. Never touches the graph's YAML content."""
    logger = get_server_logger()
    try:
        save_vuegraph_layout(payload.filename, payload.layout)
    except Exception as exc:
        logger.error(
            "Failed to persist Vue graph layout",
            log_type=LogType.ERROR,
            filename=payload.filename,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to save graph layout")

    logger.info(
        "Vue graph layout saved",
        log_type=LogType.REQUEST,
        filename=payload.filename,
    )
    return {"filename": payload.filename, "status": "saved"}


# Declared BEFORE the generic /{filename} route so the more specific path always
# wins, regardless of how the router orders its matches.
@router.get("/api/vuegraphs/{filename}/layout")
async def get_vuegraph_layout(filename: str):
    """Return a saved canvas layout.

    A missing layout is NOT an error — it is the normal answer for a graph nobody
    has arranged yet, and the client's correct response is to compute a fresh one.
    So this returns 200 with layout=null rather than 404, which keeps "never
    arranged" distinguishable from "the lookup failed".
    """
    logger = get_server_logger()
    try:
        layout = fetch_vuegraph_layout(filename)
    except Exception as exc:
        logger.error(
            "Failed to load Vue graph layout",
            log_type=LogType.ERROR,
            filename=filename,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to read graph layout")

    return {"filename": filename, "layout": layout}


@router.get("/api/vuegraphs/{filename}")
async def get_vuegraph_content(filename: str):
    logger = get_server_logger()
    try:
        content = fetch_vuegraph_content(filename)
    except Exception as exc:
        logger.error(
            "Failed to load Vue graph content",
            log_type=LogType.ERROR,
            filename=filename,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to read graph content")

    if content is None:
        raise HTTPException(status_code=404, detail="Graph content not found")

    logger.info(
        "Vue graph content fetched",
        log_type=LogType.REQUEST,
        filename=filename,
    )
    return {"filename": filename, "content": content}
