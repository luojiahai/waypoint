from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()


@router.get("/sync", response_class=HTMLResponse)
def sync(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "sync"})
    return templates.TemplateResponse(request, "sync.html", {"ctx": ctx, "page": "sync"})
