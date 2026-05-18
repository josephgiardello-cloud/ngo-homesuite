from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ngo_homesuite.models.core import Donation, P2PPageDonation, db
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

    linked_donations = list(
        db.session.scalars(
            select(Donation)
            .join(P2PPageDonation, P2PPageDonation.donation_id == Donation.id)
            .where(P2PPageDonation.page_id == page.id, Donation.organization_id == page.organization_id)
            .order_by(Donation.donation_date.desc(), Donation.id.desc())
            .limit(7)
        )
    )
    gift_count = int(
        db.session.scalar(
            select(func.count(P2PPageDonation.donation_id)).where(P2PPageDonation.page_id == page.id)
        )
        or 0
    )
    raised = float(progress.get("total_raised", 0.0) or 0.0)
    goal_amount = float(page.goal_amount or 0.0)
    average_gift = (raised / gift_count) if gift_count > 0 else 0.0
    amount_left = max(goal_amount - raised, 0.0)
    pct = float(progress.get("pct_of_goal", 0.0) or 0.0)
    match_ratio = float(getattr(page, "match_ratio", 0.0) or 0.0)
    match_cap = float(getattr(page, "match_cap_amount", 0.0) or 0.0)
    challenge_goal = float(getattr(page, "challenge_goal_amount", 0.0) or 0.0)
    challenge_end_date = getattr(page, "challenge_end_date", None)

    effective_match_value = 0.0
    if match_ratio > 0 and match_cap > 0:
        effective_match_value = min(raised * match_ratio, match_cap)
    challenge_pct = round((raised / challenge_goal) * 100, 1) if challenge_goal > 0 else 0.0

    milestone_markers = [25, 50, 75, 100]
    milestone_rows = [{"pct": m, "reached": pct >= m} for m in milestone_markers]

    fallback_amounts = [25, 50, 100, 250]
    if goal_amount >= 1000:
        fallback_amounts = [50, 100, 250, 500]

    public_url = request.base_url
    encoded_share_text = f"Support {page.title}"

    days_live = None
    created_at = getattr(page, "created_at", None)
    if isinstance(created_at, datetime):
        now_naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        days_live = max((now_naive_utc - created_at).days, 0)

    supporter_notes = []
    for donation in linked_donations:
        note_text = str(getattr(donation, "notes", "") or "").strip()
        if not note_text:
            continue
        snippet = note_text[:120]
        if len(note_text) > 120:
            snippet += "..."
        supporter_notes.append(
            {
                "display_name": "Anonymous Supporter" if bool(getattr(donation, "is_anonymous", False)) else (getattr(donation, "donor_name", None) or "Supporter"),
                "snippet": snippet,
            }
        )
        if len(supporter_notes) >= 4:
            break

    return render_template(
        "p2p_public_page.html",
        page=page,
        progress=progress,
        leaderboard_rows=page_leaderboard,
        linked_donations=linked_donations,
        gift_count=gift_count,
        average_gift=average_gift,
        amount_left=amount_left,
        milestone_rows=milestone_rows,
        donation_tiers=fallback_amounts,
        public_url=public_url,
        share_text=encoded_share_text,
        match_ratio=match_ratio,
        match_cap=match_cap,
        effective_match_value=effective_match_value,
        challenge_goal=challenge_goal,
        challenge_pct=challenge_pct,
        challenge_end_date=challenge_end_date,
        supporter_notes=supporter_notes,
        days_live=days_live,
        active_page="give",
        is_embed=request.args.get("embed", "0") == "1",
    )


@p2p_bp.get("/<string:slug>/embed.js")
def p2p_embed_script(slug: str):
    from ngo_homesuite.services.p2p_service import get_page_by_slug

    page = get_page_by_slug(slug)
    if page is None or page.status != "active":
        return Response("window.ngoHomeSuiteP2PEmbedError='not found';", mimetype="application/javascript", status=404)

    # Use an origin-relative path so untrusted Host headers can't taint embed output.
    src = f"/p2p/{page.public_slug}?embed=1"
    safe_src = json.dumps(src)
    safe_title = json.dumps((f"Fundraiser: {page.title}")[:180])
    script = f"""
(function() {{
  var script = document.currentScript;
  if (!script) return;
  var targetId = script.getAttribute('data-target');
  var target = targetId ? document.getElementById(targetId) : script.parentNode;
  if (!target) return;
  var iframe = document.createElement('iframe');
    iframe.src = {safe_src};
    iframe.title = {safe_title};
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

    limit = max(1, min(request.args.get("limit", 10, type=int) or 10, 100))
    offset = max(0, request.args.get("offset", 0, type=int) or 0)
    campaign_slug = request.args.get("campaign_slug")
    return jsonify(leaderboard(_org_id(), campaign_slug=campaign_slug, limit=limit, offset=offset))
