"""Loopback-only HTTP entrypoint. Run one process; no external authentication."""
import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
import torch
import uvicorn

from .engine import Conflict, Engine, Missing

STATIC = Path(__file__).parent / 'static'


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PageInput(Payload):
    title: str = Field(default='無題のページ', max_length=200)
    body: str = Field(default='', max_length=65536)


class PageEdit(PageInput):
    revision: int = Field(ge=1)


class Feed(Payload):
    page_id: str = Field(min_length=1, max_length=32)
    revision: int = Field(ge=1)
    repeats: int = Field(default=2, ge=1, le=10)
    request_key: str = Field(min_length=1, max_length=128)


class Generation(Payload):
    revision: int = Field(ge=1)
    max_new_tokens: int = Field(default=64, ge=1, le=128)
    temperature: float = Field(default=0.8, ge=0, le=2, allow_inf_nan=False)
    seed: int = Field(default=42, ge=0, le=2147483647)


def create_app(data_dir=None, worker=True):
    root = Path(data_dir or os.environ.get('TRACE_DATA_DIR',
                str(Path.home() / '.local' / 'share' / 'trace-llm')))

    @asynccontextmanager
    async def lifespan(app):
        engine = Engine(root)
        app.state.engine = engine
        if worker:
            engine.start()
        try:
            yield
        finally:
            engine.close()

    app = FastAPI(title='TraceLLM', version='0.1.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if request.headers.get('x-trace-request') != '1':
                return JSONResponse({'detail': 'Missing local request header'}, status_code=403)
            origin = request.headers.get('origin')
            expected = urlsplit(str(request.base_url))
            if origin:
                actual = urlsplit(origin)
                if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                    return JSONResponse({'detail': 'Cross-origin request rejected'}, status_code=403)
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 512000:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
            # BaseHTTPMiddleware reuses the cached body for downstream validation.
            request._body = bytes(data)
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.get('/api/healthz')
    def health():
        return {'status': 'ok', 'mode': 'local-single-user'}

    @app.get('/api/status')
    def status():
        return app.state.engine.status()

    @app.get('/api/pages')
    def pages():
        return app.state.engine.pages()

    @app.post('/api/pages', status_code=201)
    def create_page(data: PageInput):
        return app.state.engine.create_page(data.title, data.body)

    @app.get('/api/pages/{page_id}')
    def get_page(page_id: str):
        return app.state.engine.get_page(page_id)

    @app.put('/api/pages/{page_id}')
    def edit_page(page_id: str, data: PageEdit):
        return app.state.engine.update_page(page_id, data.title, data.body, data.revision)

    @app.delete('/api/pages/{page_id}')
    def delete_page(page_id: str):
        app.state.engine.delete_page(page_id)
        return {'deleted': True, 'unlearned': False, 'historical_revisions_retained': True}

    @app.post('/api/pages/{page_id}/responses', status_code=201)
    def respond(page_id: str, data: Generation):
        return app.state.engine.respond(page_id, data.revision, data.max_new_tokens,
                                        data.temperature, data.seed)

    @app.get('/api/pages/{page_id}/responses')
    def responses(page_id: str):
        return app.state.engine.responses(page_id)

    @app.post('/api/feed', status_code=202)
    def feed(data: Feed):
        return app.state.engine.feed(data.page_id, data.revision, data.repeats, data.request_key)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        app.state.engine.cancel(job_id)
        return {'cancelled': True}

    @app.get('/api/history')
    def history():
        return app.state.engine.history()

    @app.post('/api/checkpoints/{checkpoint_id}/restore')
    def restore(checkpoint_id: str):
        return app.state.engine.restore(checkpoint_id)

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app


def main():
    parser = argparse.ArgumentParser(description='TraceLLM: local scratch language model journal')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.threads <= 32:
        parser.error('Invalid port or thread count')
    os.umask(0o077)
    torch.set_num_threads(args.threads)
    uvicorn.run(create_app(args.data_dir), host='127.0.0.1', port=args.port,
                workers=1, access_log=False)


if __name__ == '__main__':
    main()
