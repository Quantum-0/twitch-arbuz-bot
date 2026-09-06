import sqlalchemy as sa

from database.models import CharacterInfo, TwitchUserSettings, User


def build_public_references_query() -> sa.Select:
    """Build the reference gallery query, respecting each owner's privacy setting."""
    return (
        sa.select(CharacterInfo, User.login_name, User.profile_image_url)
        .join(User, sa.func.lower(User.login_name) == sa.func.lower(CharacterInfo.name))
        .join(TwitchUserSettings, TwitchUserSettings.user_id == User.id)
        .where(TwitchUserSettings.ai_reference_show_in_profile.is_(True))
        .where(sa.or_(CharacterInfo.file_id.is_not(None), CharacterInfo.description.is_not(None)))
        .order_by(User.login_name.asc())
    )
