"""Viewer package — creates the Flask app, with optional HTTP Basic Auth."""
import os
import secrets

from flask import Flask, Response, request

from viewer.routes import bp


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ─── HTTP Basic Auth ─────────────────────────────────────────
    # Set VIEWER_USER / VIEWER_PASS in .env to enable.
    # Leave both empty to disable (useful for localhost-only dev).
    auth_user = os.getenv("VIEWER_USER", "")
    auth_pass = os.getenv("VIEWER_PASS", "")
    auth_enabled = bool(auth_user and auth_pass)

    @app.before_request
    def _require_auth():
        if not auth_enabled:
            return None

        auth = request.authorization
        if not auth:
            return _unauthorized()

        # Constant-time compare → no timing side-channel
        user_ok = secrets.compare_digest(auth.username or "", auth_user)
        pass_ok = secrets.compare_digest(auth.password or "", auth_pass)
        if not (user_ok and pass_ok):
            return _unauthorized()
        return None

    def _unauthorized() -> Response:
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Media Viewer", charset="UTF-8"'},
        )

    app.register_blueprint(bp)
    return app