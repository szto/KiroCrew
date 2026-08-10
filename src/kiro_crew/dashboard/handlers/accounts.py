"""Dashboard endpoint for Claude account profiles.

Deliberately read-only and name-only: adding or authenticating an account is a
``claude login`` in a terminal, not something the dashboard should broker. The
response NEVER carries a config dir or credential bytes — the dropdown only needs
a name and whether it can start a session.
"""

from __future__ import annotations

import logging

from aiohttp import web

from kiro_crew.accounts import list_accounts, resolve_account
from kiro_crew.config.loader import KiroCrewConfig

logger = logging.getLogger(__name__)


async def api_accounts_get(request: web.Request) -> web.Response:
    """GET /api/accounts — declared account profiles and their login state."""
    del request  # config comes from disk, the same as every other handler here
    cfg = KiroCrewConfig.load()
    try:
        active = resolve_account(cfg).name
    except Exception:
        # A misconfigured active account must not blank the whole list: the user
        # needs to SEE the profiles in order to pick a working one.
        logger.warning("active account did not resolve; listing anyway", exc_info=True)
        active = ""
    return web.json_response(
        {
            "provider": cfg.agent.provider,
            "active": active,
            "accounts": [{"name": a.name, "logged_in": a.logged_in} for a in list_accounts(cfg)],
        }
    )
