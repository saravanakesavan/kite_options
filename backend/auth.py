"""
Authentication and security utilities
"""

from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
import bcrypt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from models import User
from database import get_db
import os
import hashlib
import base64
import logging

logger = logging.getLogger(__name__)

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480   # 8 hours — single user, long sessions

# Config-based admin (survives DB wipe)
ADMIN_USERNAME     = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

# Token scheme
security = HTTPBearer()

def _prepare_password(password: str) -> bytes:
    """
    SHA-256 pre-hash the password before bcrypt to handle passwords longer
    than bcrypt's 72-byte limit without any loss of entropy.
    Returns bytes ready to pass directly to bcrypt.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)  # 44 bytes — always under the 72-byte limit

class AuthService:
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(_prepare_password(plain_password), hashed_password.encode("utf-8"))

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password"""
        return bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt()).decode("utf-8")
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
        """Create a JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def verify_token(token: str) -> Optional[dict]:
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                return None
            return payload
        except JWTError:
            return None
    
    @staticmethod
    def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
        """
        Authenticate user with username and password.

        Falls back to env-based admin credentials so the admin can always
        log in even if the SQLite file is wiped and recreated.  On a
        successful env-based login the admin row is auto-inserted so the
        rest of the app (JWTs, orders FK, etc.) works normally.
        """
        user = db.query(User).filter(User.username == username).first()

        if user:
            # Normal DB-backed auth
            if not AuthService.verify_password(password, user.hashed_password):
                return None
            return user

        # ── Fallback: env-based admin ──────────────────────────────────────
        if (
            ADMIN_PASSWORD_HASH
            and username == ADMIN_USERNAME
            and bcrypt.checkpw(_prepare_password(password), ADMIN_PASSWORD_HASH.encode("utf-8"))
        ):
            # DB row missing (e.g. fresh DB) — auto-create so FKs work
            logger.info("Admin not in DB; auto-creating from env config")
            user = ensure_admin_in_db(db)
            return user

        return None

    @staticmethod
    def create_user(db: Session, username: str, email: str, password: str) -> User:
        """Create a new user"""
        # Check if user already exists
        existing_user = db.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()
        
        if existing_user:
            raise HTTPException(
                status_code=400,
                detail="Username or email already registered"
            )
        
        # Create new user
        hashed_password = AuthService.get_password_hash(password)
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password
        )
        
        db.add(user)
        db.commit()
        db.refresh(user)
        
        return user

def ensure_admin_in_db(db: Session) -> User:
    """
    Guarantee the config-based admin user exists in the DB.
    Called on server startup and on first env-based login after a DB wipe.
    If the row already exists it is returned unchanged; otherwise it is
    created with the hash from ADMIN_PASSWORD_HASH (no re-hashing needed).
    """
    existing = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if existing:
        return existing

    if not ADMIN_PASSWORD_HASH:
        logger.warning("ADMIN_PASSWORD_HASH not set — skipping admin user creation")
        return None

    admin = User(
        username=ADMIN_USERNAME,
        email=f"{ADMIN_USERNAME}@local",
        hashed_password=ADMIN_PASSWORD_HASH,   # already hashed in .env
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info(f"Admin user '{ADMIN_USERNAME}' created in DB from env config")
    return admin


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Dependency to get current authenticated user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        payload = AuthService.verify_token(token)
        if payload is None:
            raise credentials_exception
        
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
            
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    
    return user

def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Dependency to get current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user