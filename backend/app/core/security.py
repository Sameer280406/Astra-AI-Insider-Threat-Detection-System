from datetime import timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .config import settings
from .db import get_db, set_tenant
from ..domain.entities import User, Session, now
ph=PasswordHasher()
ROLES={'platform_admin','org_admin','soc_manager','soc_analyst','auditor','viewer'}
PERMISSIONS={
'viewer':{'read'},'auditor':{'read','audit','report'},
'soc_analyst':{'read','triage','feedback','report'},
'soc_manager':{'read','triage','feedback','report','policy','contain','audit'},
'org_admin':{'read','triage','feedback','report','policy','contain','audit','admin','ingest'},
'platform_admin':{'read','triage','feedback','report','policy','contain','audit','admin','ingest'}}
def verify(password,hashed):
    try: return ph.verify(hashed,password)
    except (VerifyMismatchError,InvalidHashError): return False

def principal(request:Request,db=Depends(get_db)):
    token=request.cookies.get('astra_session')
    auth=request.headers.get('authorization','')
    if auth.startswith('Bearer '): token=auth[7:]
    if not token:
        raise HTTPException(401,'Authentication required')
    try:
        claims=jwt.decode(token,settings().jwt_secret,algorithms=['HS256'],audience='astra',issuer='astra',options={'require':['exp','sub','tid','jti']})
        set_tenant(db,claims['tid'])
        user=db.scalar(select(User).where(User.id==claims['sub'],User.tenant_id==claims['tid']))
        session=db.scalar(select(Session).where(Session.id==claims['jti'],Session.tenant_id==claims['tid'],Session.user_id==claims['sub']))
        if not user or not session: raise ValueError()
        exp = session.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now(): raise ValueError()
        return user
    except (jwt.PyJWTError,ValueError,TypeError): raise HTTPException(401,'Authentication required')

def require(permission):
    def check(user=Depends(principal)):
        if permission not in PERMISSIONS.get(user.role,set()): raise HTTPException(403,'Permission denied')
        return user
    return check

def new_session(db,user):
    expires=now()+timedelta(minutes=settings().session_minutes)
    row=Session(tenant_id=user.tenant_id,user_id=user.id,expires_at=expires);db.add(row);db.flush()
    return jwt.encode({'sub':user.id,'tid':user.tenant_id,'jti':row.id,'exp':expires,'iat':now(),'aud':'astra','iss':'astra'},settings().jwt_secret,algorithm='HS256')
