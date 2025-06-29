# schemas.py
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import date, time

# Schema para criação de usuário (o que a API recebe)
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str

# Schema para leitura de usuário (o que a API retorna)
class User(BaseModel):
    id: int
    name: str
    email: EmailStr

    class Config:
        orm_mode = True # Permite que o Pydantic leia o objeto SQLAlchemy

# Schemas para Partida
class MatchCreate(BaseModel):
    field_id: int
    title: str
    description: Optional[str] = None
    date: date
    start_time: time
    end_time: time
    max_players: int

class Match(BaseModel):
    id: int
    field_id: int
    creator_id: int
    title: str
    status: str

    class Config:
        orm_mode = True

# (Crie schemas para Field, Locador, etc.)