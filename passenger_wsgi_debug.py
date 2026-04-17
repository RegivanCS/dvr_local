import sys
import os
import traceback
from io import BytesIO

try:
    from app import app as _app
    _app.config['PROPAGATE_EXCEPTIONS'] = True
    _import_error = None
except Exception:
    _app = None
    _import_error = traceback.format_exc()

class DebugMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        caught = []

        def catching_start_response(status, headers, exc_info=None):
            caught.append(status)
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, catching_start_response)
            return result
        except Exception:
            error_msg = traceback.format_exc()
            body = (
                f"ERRO NA ROTA:\n"
                f"METODO: {environ.get('REQUEST_METHOD')}\n"
                f"PATH: {environ.get('PATH_INFO')}\n\n"
                f"{error_msg}"
            ).encode('utf-8')
            start_response('500 Internal Server Error', [
                ('Content-Type', 'text/plain; charset=utf-8'),
                ('Content-Length', str(len(body)))
            ])
            return [body]

def application(environ, start_response):
    if _import_error:
        body = f"ERRO DE IMPORTACAO:\n\n{_import_error}".encode('utf-8')
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/plain; charset=utf-8'),
            ('Content-Length', str(len(body)))
        ])
        return [body]
    return DebugMiddleware(_app)(environ, start_response)
