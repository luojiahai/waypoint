from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()


@router.get("/delivery", response_class=HTMLResponse)
def delivery(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "delivery"})
    return templates.TemplateResponse(request, "delivery.html", {"ctx": ctx, "page": "delivery"})
