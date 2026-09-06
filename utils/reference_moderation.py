from fastapi import HTTPException

from database.models import User

# TODO: move reference moderation roles to the database.
REFERENCE_MODERATOR_LOGINS = frozenset(
    {
        "quantum075",
        "fufigg",
        "beatr1xo",
        "toad_anna",
        "anna_toad",
        "d_e_l_y",
        "fra3a",
    }
)
REFERENCE_ADMIN_LOGINS = frozenset({"quantum075"})
MIN_REFERENCE_FOLLOWERS = 20


def is_reference_moderator(login: str) -> bool:
    return login.lower() in REFERENCE_MODERATOR_LOGINS


def is_reference_admin(login: str) -> bool:
    return login.lower() in REFERENCE_ADMIN_LOGINS


def ensure_reference_moderator(user: User) -> User:
    if not is_reference_moderator(user.login_name):
        raise HTTPException(status_code=403, detail="No access to reference moderation")
    return user


def ensure_reference_upload_allowed(user: User) -> None:
    if is_reference_admin(user.login_name):
        return
    if user.followers_count is None or user.followers_count < MIN_REFERENCE_FOLLOWERS:
        raise HTTPException(
            status_code=403,
            detail=f"Reference upload is available to streamers with at least {MIN_REFERENCE_FOLLOWERS} followers.",
        )
