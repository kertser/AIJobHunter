"""Reports router — browse and view daily reports."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from job_hunter.web.deps import get_settings, get_user_data_dir

router = APIRouter(tags=["reports"])


@router.get("/reports")
async def reports_page(request: Request):
    templates = request.app.state.templates
    data_dir = get_user_data_dir(request)
    reports = _list_reports(data_dir)
    return templates.TemplateResponse(request, "reports.html", {
        "reports": reports,
    })


@router.get("/api/reports")
async def list_reports(request: Request):
    data_dir = get_user_data_dir(request)
    return {"reports": _list_reports(data_dir)}


@router.get("/api/reports/{date}")
async def get_report(date: str, request: Request):
    data_dir = get_user_data_dir(request)
    json_path = data_dir / "reports" / f"{date}.json"
    md_path = data_dir / "reports" / f"{date}.md"

    if not json_path.exists():
        raise HTTPException(404, f"No report found for {date}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    md_content = ""
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")

    # If HTML request, render template
    if "text/html" in request.headers.get("accept", ""):
        templates = request.app.state.templates
        return templates.TemplateResponse(request, "report_detail.html", {
            "report": data, "md_content": md_content, "date": date,
        })

    return data


def _list_reports(data_dir: Path) -> list[dict]:
    reports_dir = data_dir / "reports"
    if not reports_dir.exists():
        return []
    files = sorted(reports_dir.glob("*.json"), reverse=True)
    return [{"date": f.stem, "path": str(f)} for f in files]
