"""Session-authenticated browser pages."""

from __future__ import annotations

from functools import wraps
from urllib.parse import urljoin, urlparse
from uuid import UUID

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

from flaskapp.database import authenticate_user, create_user
from flaskapp.countries import COUNTRIES
from flaskapp.places import city_options
from flaskapp.forms import LoginForm, RegistrationForm
from flaskapp.admin_auth import is_admin_email

pages_bp = Blueprint("pages", __name__)


def _is_safe_redirect(target: str) -> bool:
    """Allow only same-origin redirects to prevent open-redirect attacks."""
    host = urlparse(request.host_url)
    candidate = urlparse(urljoin(request.host_url, target))
    return candidate.scheme in {"http", "https"} and candidate.netloc == host.netloc


def login_required(view):
    """Redirect anonymous browser users to the sign-in page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("pages.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


@pages_bp.route("/", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("pages.main"))

    form = LoginForm()
    if form.validate_on_submit():
        user = authenticate_user(form.email.data, form.password.data)
        if user:
            session.clear()
            session["authenticated"] = True
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_name"] = user["name"] or "Traveller"
            session["user_country"] = user["country"] or ""
            session.permanent = bool(form.remember.data)
            # _is_safe_redirect() restricts the target to the site's own netloc,
            # which is the exact mitigation this rule asks for.
            destination = request.args.get("next", "")  # nosemgrep: python.flask.security.open-redirect.open-redirect
            if destination and _is_safe_redirect(destination):
                return redirect(destination)
            return redirect(url_for("pages.main"))
        flash("The email or password is incorrect.", "danger")

    return render_template("login.html", form=form)


@pages_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("authenticated"):
        return redirect(url_for("pages.main"))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = create_user(
            form.name.data,
            form.email.data,
            generate_password_hash(form.password.data),
            form.country.data,
            form.birthday.data.isoformat(),
        )
        if user is None:
            form.email.errors.append("An account with this email already exists.")
        else:
            session.clear()
            session["authenticated"] = True
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_name"] = user["name"]
            session["user_country"] = user["country"] or ""
            flash("Your account has been created.", "success")
            return redirect(url_for("pages.main"))

    return render_template("register.html", form=form)


@pages_bp.get("/main")
@login_required
def main():
    # `city_options` is embedded in the page rather than fetched, so the
    # dependent city <select> repopulates instantly and the form still works if
    # a later request fails. It is public reference data — no per-user content.
    return render_template(
        "main.html", user_name=session.get("user_name", "Traveller"), countries=COUNTRIES,
        is_admin=is_admin_email(current_app.config, session.get("user_email")),
        user_country=session.get("user_country", ""),
        city_options=city_options(),
    )


@pages_bp.get("/chat/<uuid:request_id>")
@login_required
def chat(request_id: UUID):
    return render_template(
        "chat.html", user_name=session.get("user_name", "Traveller"), request_id=request_id,
        intake_mode=False,
        is_admin=is_admin_email(current_app.config, session.get("user_email")),
    )


@pages_bp.get("/chat/intake")
@login_required
def intake_chat():
    """Host clarification before a complete request is sent to specialist agents."""
    return render_template(
        "chat.html", user_name=session.get("user_name", "Traveller"), countries=COUNTRIES,
        intake_mode=True,
        is_admin=is_admin_email(current_app.config, session.get("user_email")),
    )


@pages_bp.get("/admin")
@login_required
def admin():
    if not is_admin_email(current_app.config, session.get("user_email")):
        abort(403)
    return render_template("admin.html", user_name=session.get("user_name", "Administrator"))


@pages_bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been signed out safely.", "success")
    return redirect(url_for("pages.login"))
