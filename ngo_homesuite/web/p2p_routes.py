from __future__ import annotations

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


p2p_bp = Blueprint("p2p", __name__, url_prefix="/p2p")


def _org_id() -> int:
    return int(current_user.organization_id)


def _prefers_json() -> bool:
    # Keep API behavior for JSON clients while allowing browsers to render HTML.
    return request.accept_mimetypes.best == "application/json"


@p2p_bp.get("/pages")
@login_required
def list_pages_route():
    from ngo_homesuite.services.p2p_service import list_pages

    pages = list_pages(_org_id(), status=request.args.get("status"))
    return jsonify([
        {
            "id": p.id,
            "title": p.title,
            "public_slug": p.public_slug,
            "status": p.status,
            "goal_amount": p.goal_amount,
            "donor_id": p.donor_id,
        }
        for p in pages
    ])


@p2p_bp.post("/pages")
@login_required
@roles_required("admin", "staff")
def create_page_route():
    from ngo_homesuite.services.p2p_service import create_page

    data = request.get_json(silent=True) or {}
    if data.get("donor_id") is None or not data.get("title"):
        return jsonify({"error": "donor_id and title are required"}), 400

    try:
        page = create_page(_org_id(), **data)
    except ValueError:
        return jsonify({"error": "Invalid resource reference"}), 400

    return jsonify({"id": page.id, "public_slug": page.public_slug, "status": page.status}), 201


@p2p_bp.get("/pages/<int:page_id>")
@login_required
def get_page_route(page_id: int):
    from ngo_homesuite.services.p2p_service import get_page, get_progress

    page = get_page(page_id, _org_id())
    if page is None:
        return jsonify({"error": "not found"}), 404

    progress = get_progress(page_id, _org_id())
    return jsonify(
        {
            "id": page.id,
            "title": page.title,
            "public_slug": page.public_slug,
            "status": page.status,
            "goal_amount": page.goal_amount,
            "progress": progress,
        }
    )


@p2p_bp.post("/pages/<int:page_id>/publish")
@login_required
@roles_required("admin", "staff")
def publish_page_route(page_id: int):
    from ngo_homesuite.services.p2p_service import publish_page

    page = publish_page(page_id, _org_id())
    return jsonify({"id": page.id, "status": page.status})


@p2p_bp.post("/pages/<int:page_id>/close")
@login_required
@roles_required("admin", "staff")
def close_page_route(page_id: int):
    from ngo_homesuite.services.p2p_service import close_page

    page = close_page(page_id, _org_id())
    return jsonify({"id": page.id, "status": page.status})


@p2p_bp.get("/<string:slug>")
def public_page_route(slug: str):
    from ngo_homesuite.services.p2p_service import get_page_by_slug, get_progress, leaderboard

    page = get_page_by_slug(slug)
    if page is None or page.status != "active":
        if _prefers_json():
            return jsonify({"error": "not found"}), 404
        return render_template("errors/404.html"), 404

    progress = get_progress(page.id, page.organization_id)
    payload = {
        "id": page.id,
        "title": page.title,
        "story": page.story,
        "goal_amount": page.goal_amount,
        "progress": progress,
    }
    if _prefers_json():
        return jsonify(payload)

    page_leaderboard = leaderboard(
        page.organization_id,
        campaign_slug=page.campaign_slug,
        limit=8,
        offset=0,
    )
    return render_template(
        "p2p_public_page.html",
        page=page,
        progress=progress,
        leaderboard_rows=page_leaderboard,
        active_page="give",
        is_embed=request.args.get("embed", "0") == "1",
    )


@p2p_bp.get("/<string:slug>/embed.js")
def p2p_embed_script(slug: str):
    from ngo_homesuite.services.p2p_service import get_page_by_slug

    page = get_page_by_slug(slug)
    if page is None or page.status != "active":
        return Response("window.ngoHomeSuiteP2PEmbedError='not found';", mimetype="application/javascript", status=404)

    src = request.url_root.rstrip("/") + f"/p2p/{page.public_slug}?embed=1"
    script = f"""
(function() {{
  var script = document.currentScript;
  if (!script) return;
  var targetId = script.getAttribute('data-target');
  var target = targetId ? document.getElementById(targetId) : script.parentNode;
  if (!target) return;
  var iframe = document.createElement('iframe');
  iframe.src = {src!r};
  iframe.title = 'Fundraiser: {page.title}'.slice(0, 180);
  iframe.width = '100%';
  iframe.height = script.getAttribute('data-height') || '560';
  iframe.style.border = '0';
  iframe.style.maxWidth = '100%';
  iframe.style.borderRadius = '12px';
  iframe.loading = 'lazy';
  target.appendChild(iframe);
}})();
""".strip()
    return Response(script, mimetype="application/javascript")


@p2p_bp.get("/leaderboard")
@login_required
def leaderboard_route():
    from ngo_homesuite.services.p2p_service import leaderboard

    limit = request.args.get("limit", 10, type=int)
    offset = request.args.get("offset", 0, type=int)
    campaign_slug = request.args.get("campaign_slug")
    return jsonify(leaderboard(_org_id(), campaign_slug=campaign_slug, limit=limit, offset=offset))
