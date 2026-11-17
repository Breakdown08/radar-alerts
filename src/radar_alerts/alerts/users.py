from rest_framework.request import Request

from radar_alerts.alerts.models import User


def current_user(request: Request) -> User:
    """The authenticated user of a request; the views require authentication, so it is always a User."""
    user: object = request.user
    assert isinstance(user, User)
    return user
