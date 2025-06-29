from sqlalchemy.orm import Session
from . import models, schemas
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()

def create_user(db: Session, user: schemas.UserCreate):
    hashed_password = get_password_hash(user.password)
    db_user = models.User(email=user.email, name=user.name, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_match(db: Session, match: schemas.MatchCreate, creator_id: int):
    # Lógica para criar a partida no banco de dados
    db_match = models.Match(**match.dict(), creator_id=creator_id)
    db.add(db_match)
    db.commit()
    db.refresh(db_match)
    return db_match

def get_field(db: Session, field_id: int):
    return db.query(models.Field).filter(models.Field.id == field_id).first()