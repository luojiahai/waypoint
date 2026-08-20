from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from waypoint.web.deps import PageContext, page_context, templates

router = APIRouter()


@router.get("/people", response_class=HTMLResponse)
def people(request: Request, ctx: PageContext = Depends(page_context)) -> HTMLResponse:
    if not ctx.synced:
        return templates.TemplateResponse(request, "empty.html", {"ctx": ctx, "page": "people"})
    return templates.TemplateResponse(request, "people.html", {"ctx": ctx, "page": "people"})
