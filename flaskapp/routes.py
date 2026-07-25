"""Session-authenticated browser pages."""

from __future__ import annotations

from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from flaskapp.forms import LoginForm

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
        return redirect(url_for("pages.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        valid_email = form.email.data.lower() == current_app.config["LOGIN_EMAIL"]
        valid_password = check_password_hash(
            current_app.config["LOGIN_PASSWORD_HASH"], form.password.data
        )
        if valid_email and valid_password:
            session.clear()
            session["authenticated"] = True
            session["user_email"] = form.email.data.lower()
            session.permanent = bool(form.remember.data)
            # _is_safe_redirect() restricts the target to the site's own netloc,
            # which is the exact mitigation this rule asks for.
            destination = request.args.get("next", "")  # nosemgrep: python.flask.security.open-redirect.open-redirect
            if destination and _is_safe_redirect(destination):
                return redirect(destination)
            return redirect(url_for("pages.dashboard"))
        flash("The email or password is incorrect.", "danger")

    return render_template("login.html", form=form)


@pages_bp.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user_email=session.get("user_email"))


@pages_bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been signed out safely.", "success")
    return redirect(url_for("pages.login"))
