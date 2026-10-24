import logging,time,uuid
from collections import defaultdict,deque
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from .core.config import settings
from .core.db import engine
from .models.gateway import ModelUnavailable
from .api.routes import router
logging.basicConfig(level=logging.INFO,format='%(message)s')
logger=logging.getLogger('astra')
app=FastAPI(title='Astra Intelligence API',version='1.0.0',description='Tenant-scoped insider risk API. Mock predictions are synthetic, not trained model output.')
app.add_middleware(CORSMiddleware,allow_origins=settings().allowed_origins,allow_credentials=True,allow_methods=['GET','POST','PATCH','PUT','DELETE','OPTIONS'],allow_headers=['Content-Type','Authorization','X-Astra-Request'])
limits = defaultdict(deque)
redis_client = None
if settings().redis_url:
    from redis import Redis
    redis_client = Redis.from_url(settings().redis_url)

def get_rate_identity(request: Request) -> tuple[str, str]:
    """Returns (identifier, category) for rate-limiting."""
    path = request.url.path
    if path in {'/health', '/docs', '/openapi.json', '/favicon.ico'}:
        return ('health', 'exempt')
    
    # Extract client IP (respecting forward proxies)
    forwarded = request.headers.get('x-forwarded-for')
    ip = forwarded.split(',')[0].strip() if forwarded else (request.client.host if request.client else 'unknown')
    
    # Try lightweight token extraction for tenant/user isolation
    tid, uid = None, None
    token = request.cookies.get('astra_session') or (request.headers.get('authorization', '')[7:] if request.headers.get('authorization', '').startswith('Bearer ') else None)
    if token:
        try:
            import jwt
            claims = jwt.decode(token, settings().jwt_secret, algorithms=['HS256'], audience='astra', issuer='astra', options={'verify_exp': False})
            tid, uid = claims.get('tid'), claims.get('sub')
        except Exception:
            pass
            
    scope = f"{tid}:{uid}" if tid and uid else f"ip:{ip}"
    
    if path.startswith('/api/v1/auth/'):
        return (f"auth:{ip}", 'auth')
    elif path == '/api/v1/stream':
        return (f"stream:{scope}", 'stream')
    elif path == '/api/v1/demo/simulate':
        return (f"simulate:{scope}", 'simulate')
    elif request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return (f"write:{scope}", 'write')
    else:
        return (f"read:{scope}", 'read')

LIMITS_BY_CATEGORY = {
    'exempt': 1200,
    'auth': 20,
    'stream': 30,
    'simulate': 60,
    'write': 120,
    'read': 300,
}

@app.middleware('http')
async def controls(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        origin = request.headers.get('origin')
        if (origin and origin not in settings().allowed_origins) or request.headers.get('x-astra-request') != '1':
            return JSONResponse({'detail': 'Invalid request origin or missing X-Astra-Request'}, 403)
            
    key, category = get_rate_identity(request)
    limit = LIMITS_BY_CATEGORY.get(category, settings().rate_limit_per_minute)
    
    if category != 'exempt':
        if redis_client:
            try:
                min_bucket = str(int(time.time() / 60))
                r_key = f"rate:{key}:{min_bucket}"
                count = redis_client.incr(r_key)
                if count == 1:
                    redis_client.expire(r_key, 65)
                if count > limit:
                    retry_after = max(1, 60 - int(time.time() % 60))
                    return JSONResponse({'detail': 'Rate limit exceeded', 'retryAfter': retry_after}, 429, headers={'Retry-After': str(retry_after)})
            except Exception:
                return JSONResponse({'detail': 'Rate limiter unavailable'}, 503)
        else:
            bucket = limits[key]
            current = time.monotonic()
            while bucket and bucket[0] < current - 60:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(60 - (current - bucket[0])))
                return JSONResponse({'detail': 'Rate limit exceeded', 'retryAfter': retry_after}, 429, headers={'Retry-After': str(retry_after)})
            bucket.append(current)
            
    if int(request.headers.get('content-length', '0')) > 1_000_000:
        return JSONResponse({'detail': 'Payload too large'}, 413)
        
    response = await call_next(request)
    response.headers.update({
        'X-Request-ID': request_id,
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'Cache-Control': 'no-store'
    })
    logger.info('request_id=%s method=%s path=%s status=%s duration_ms=%.1f', request_id, request.method, request.url.path, response.status_code, (time.monotonic() - start) * 1000)
    return response
@app.exception_handler(ModelUnavailable)
async def model_error(request,exc): return JSONResponse({'detail':str(exc),'code':'MODEL_UNAVAILABLE'},502)
@app.exception_handler(IntegrityError)
async def conflict(request,exc): return JSONResponse({'detail':'Conflicting record','code':'CONFLICT'},409)
@app.exception_handler(Exception)
async def unhandled(request,exc):
    logger.error('Unhandled error: %s',type(exc).__name__)
    return JSONResponse({'detail':'Internal server error'},500)
@app.get('/health')
def health():
    with engine.connect() as conn: conn.execute(text('SELECT 1'))
    return {'status':'ok','database':'connected','modelMode':settings().model_mode,'stream':'database_polling','queue':'synchronous','synthetic':settings().model_mode=='mock'}
from .api.containment_routes import router as response_router
from .api.pipeline_routes import router as pipeline_router
app.include_router(response_router)
app.include_router(pipeline_router)
app.include_router(router)
