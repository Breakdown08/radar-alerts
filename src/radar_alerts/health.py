from django.db import Error, connection
from django.db.backends.utils import CursorWrapper
from django.http import HttpRequest, HttpResponse, JsonResponse


def database_ok() -> bool:
    """SELECT 1 on the default database; any django.db.Error -> False."""
    try:
        cursor: CursorWrapper = connection.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            cursor.close()
    except Error:
        return False
    return True


def healthz(request: HttpRequest) -> HttpResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    if database_ok():
        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "database unavailable"}, status=503)
