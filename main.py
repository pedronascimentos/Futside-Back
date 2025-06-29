# main.py
import os
import time
import json
import enum
from datetime import date, time as time_type, datetime
from typing import List, Optional
import firebase_admin
from firebase_admin import credentials, messaging

import paho.mqtt.client as paho
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, EmailStr
from sqlalchemy import (create_engine, Column, Integer, String, DateTime, ForeignKey,
                        DECIMAL, Date, Time, Boolean, func, Enum as SQLAlchemyEnum, UniqueConstraint)
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from passlib.context import CryptContext


# --- 1. CONFIGURAÇÕES E INICIALIZAÇÕES ---
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    # !! IMPORTANTE !! Coloque o nome exato do seu ficheiro .json aqui
    CRED_FILENAME = "futside-d414e-firebase-adminsdk-fbsvc-b53b08bd01.json" 
    cred_path = os.path.join(BASE_DIR, CRED_FILENAME)

    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
    else:
        print(f"AVISO: Ficheiro de credenciais do Firebase não encontrado em '{cred_path}'.")
        print("Notificações push estarão desabilitadas.")
except Exception as e:
    print(f"ERRO ao inicializar o Firebase Admin: {e}")

from dotenv import load_dotenv
load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("A variável de ambiente DATABASE_URL não foi definida no seu ficheiro .env!")

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 2. ENUMS ---
class DocumentTypeEnum(str, enum.Enum): CPF, CNPJ = "CPF", "CNPJ"
class SkillLevelEnum(str, enum.Enum): beginner, intermediate, advanced, professional = "beginner", "intermediate", "advanced", "professional"
class MatchStatusEnum(str, enum.Enum): scheduled, confirmed, canceled, completed = "scheduled", "confirmed", "canceled", "completed"

# --- 3. SCHEMAS PYDANTIC (Modelos da API) ---
class ConfigBase:
    from_attributes = True

class TokenRegistration(BaseModel):
    fcm_token: str

class RegionSubscription(BaseModel):
    user_id: int
    city: str

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    phone: Optional[str] = None

class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    fcm_token: Optional[str] = None
    class Config(ConfigBase): pass

class FieldCreate(BaseModel):
    name: str
    address: str
    city: str
    state: str

class FieldOut(FieldCreate):
    id: int
    locador_id: int
    class Config(ConfigBase): pass

class MatchCreate(BaseModel):
    creator_id: int
    field_id: int
    title: str
    date: date
    start_time: time_type
    end_time: time_type
    max_players: int

class MatchOut(MatchCreate):
    id: int
    status: MatchStatusEnum
    class Config(ConfigBase): pass

# --- 4. MODELOS SQLALCHEMY (Tabelas do Banco) ---
class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    phone = Column(String, nullable=True)
    fcm_token = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    locador = relationship("Locador", back_populates="user", uselist=False, cascade="all, delete-orphan")
    region_subscriptions = relationship("UserRegionSubscription", back_populates="user", cascade="all, delete-orphan")

class Locador(Base):
    __tablename__ = "locador"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), unique=True)
    user = relationship("User", back_populates="locador")
    fields = relationship("Field", back_populates="locador")

class Field(Base):
    __tablename__ = "field"
    id = Column(Integer, primary_key=True)
    locador_id = Column(Integer, ForeignKey("locador.id"))
    name = Column(String)
    address = Column(String)
    city = Column(String, index=True)
    state = Column(String)
    locador = relationship("Locador", back_populates="fields")
    matches = relationship("Match", back_populates="field")

class Match(Base):
    __tablename__ = "match"
    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("field.id"))
    creator_id = Column(Integer, ForeignKey("user.id"))
    title = Column(String)
    description = Column(String, nullable=True)
    date = Column(Date)
    start_time = Column(Time)
    end_time = Column(Time)
    max_players = Column(Integer)
    skill_level_required = Column(SQLAlchemyEnum(SkillLevelEnum), nullable=True)
    price_per_player = Column(DECIMAL, nullable=True)
    status = Column(SQLAlchemyEnum(MatchStatusEnum), default=MatchStatusEnum.scheduled)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    field = relationship("Field", back_populates="matches")
    players = relationship("PlayerMatch", back_populates="match")

class PlayerMatch(Base):
    __tablename__ = "player_match"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("match.id"))
    user_id = Column(Integer, ForeignKey("user.id"))
    joined_at = Column(DateTime, server_default=func.now())
    match = relationship("Match", back_populates="players")

class UserRegionSubscription(Base):
    __tablename__ = "user_region_subscription"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"))
    city = Column(String, index=True)
    user = relationship("User", back_populates="region_subscriptions")
    __table_args__ = (UniqueConstraint('user_id', 'city', name='_user_city_uc'),)

Base.metadata.create_all(bind=engine)

# --- 5. FUNÇÕES DE UTILIDADE E DEPENDÊNCIAS ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# CORREÇÃO: Função de notificação atualizada para usar send_each_for_multicast
def send_batch_push(tokens: List[str], title: str, body: str, data: Optional[dict] = None):
    """
    Envia a mesma notificação para múltiplos dispositivos usando o método Multicast.
    Esta é a abordagem moderna recomendada pelo Firebase.
    """
    if not firebase_admin._apps:
        print("Push não enviado: Firebase Admin não inicializado.")
        return
    
    valid_tokens = [token for token in tokens if token]
    if not valid_tokens:
        print("Nenhum token FCM válido para enviar a notificação.")
        return

    # Crie uma única mensagem multicast
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        tokens=valid_tokens,
        data=data or {}
    )
    
    try:
        # Use send_each_for_multicast para enviar a mensagem para todos os tokens
        response = messaging.send_each_for_multicast(message)
        print(f'Notificações push enviadas. Sucessos: {response.success_count}, Falhas: {response.failure_count}')
        
        if response.failure_count > 0:
            failed_tokens = []
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    # O erro pode ser visto em resp.exception
                    failed_tokens.append(valid_tokens[idx])
            print(f'Lista de tokens que causaram falhas: {failed_tokens}')

    except Exception as e:
        print(f'Erro ao enviar notificações push em lote: {e}')

# --- 6. CONFIGURAÇÃO MQTT ---
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_TOPIC_REGIONAL_BASE = "futside/matches"
MQTT_TOPIC_MATCH_BASE = "futside/match"
mqtt_client = paho.Client(client_id=f"fastapi_publisher_{int(time.time())}")

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0: print(f"Conectado ao Broker MQTT em {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    else: print(f"Falha ao conectar ao Broker MQTT, código: {rc}")

mqtt_client.on_connect = on_connect

try:
    mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"ERRO: Não foi possível conectar ao Broker MQTT: {e}")

# --- 7. INICIALIZAÇÃO E ROTAS DA API ---
app = FastAPI(title="Futside API v.Full+FCM")

@app.on_event("shutdown")
def shutdown_event():
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    print("Cliente MQTT desconectado.")

# --- ROTAS DE NOTIFICAÇÃO ---
@app.post("/users/{user_id}/register-fcm", status_code=status.HTTP_200_OK, tags=["Notifications"])
def register_fcm_token(user_id: int, payload: TokenRegistration, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user: raise HTTPException(status_code=404, detail="Usuário não encontrado")
    db_user.fcm_token = payload.fcm_token
    db.commit()
    return {"message": "Token FCM atualizado com sucesso"}

@app.post("/subscriptions/region", status_code=status.HTTP_201_CREATED, tags=["Notifications"])
def subscribe_to_region(payload: RegionSubscription, db: Session = Depends(get_db)):
    existing_sub = db.query(UserRegionSubscription).filter_by(user_id=payload.user_id, city=payload.city).first()
    if existing_sub: return {"message": "Usuário já inscrito nesta região"}
    new_sub = UserRegionSubscription(user_id=payload.user_id, city=payload.city)
    db.add(new_sub)
    db.commit()
    return {"message": f"Inscrito com sucesso em {payload.city}"}

# --- ROTAS DE USUÁRIOS ---
@app.post("/users/", response_model=UserOut, tags=["Users"])
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user: raise HTTPException(status_code=400, detail="Email já cadastrado")
    new_user = User(name=user.name, email=user.email, hashed_password=get_password_hash(user.password), phone=user.phone)
    db.add(new_user)
    db.flush() 
    new_locador = Locador(user_id=new_user.id)
    db.add(new_locador)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.get("/users/", response_model=List[UserOut], tags=["Users"])
def read_users(db: Session = Depends(get_db)):
    return db.query(User).all()

# --- ROTAS DE QUADRAS (FIELDS) ---
@app.post("/fields/", response_model=FieldOut, tags=["Fields"])
def create_field(field: FieldCreate, db: Session = Depends(get_db)):
    locador = db.query(Locador).first()
    if not locador: raise HTTPException(status_code=400, detail="Crie um usuário/locador primeiro.")
    db_field = Field(**field.dict(), locador_id=locador.id)
    db.add(db_field)
    db.commit()
    db.refresh(db_field)
    return db_field

@app.get("/fields/", response_model=List[FieldOut], tags=["Fields"])
def read_fields(db: Session = Depends(get_db)):
    return db.query(Field).all()

# --- ROTAS DE PARTIDAS (MATCHES) ---
@app.post("/matches/", response_model=MatchOut, tags=["Matches"])
def create_match(match: MatchCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    creator = db.query(User).filter(User.id == match.creator_id).first()
    if not creator: raise HTTPException(status_code=404, detail=f"Usuário criador com id {match.creator_id} não encontrado.")
    
    db_field = db.query(Field).filter(Field.id == match.field_id).first()
    if not db_field: raise HTTPException(status_code=404, detail=f"Quadra com id {match.field_id} não encontrada.")
    
    db_match = Match(**match.dict())
    db.add(db_match)
    db.commit()
    db.refresh(db_match)
    
    regional_topic = f"{MQTT_TOPIC_REGIONAL_BASE}/{db_field.city.lower().replace(' ', '-')}"
    mqtt_payload = {"event": "new_match", "data": json.loads(MatchOut.from_orm(db_match).json())}
    mqtt_client.publish(regional_topic, json.dumps(mqtt_payload, default=str), qos=1)
    print(f"MQTT Publicado em '{regional_topic}'")

    subscriptions = db.query(UserRegionSubscription.user_id).filter(func.lower(UserRegionSubscription.city) == func.lower(db_field.city)).all()
    subscribed_user_ids = {sub.user_id for sub in subscriptions}
    
    tokens_to_notify = []
    if subscribed_user_ids:
        users = db.query(User.fcm_token).filter(
            User.id.in_(subscribed_user_ids),
            User.id != creator.id,
            User.fcm_token.isnot(None)
        ).all()
        tokens_to_notify = [user.fcm_token for user in users]

    if tokens_to_notify:
        print(f"Agendando notificação push para {len(tokens_to_notify)} dispositivo(s)...")
        # Chamando a nova função send_batch_push
        background_tasks.add_task(
            send_batch_push,
            tokens=tokens_to_notify,
            title="Nova Partida na sua Área!",
            body=f"A partida '{db_match.title}' foi criada em {db_field.city}. Toque para ver!",
            data={"matchId": str(db_match.id)}
        )
    else:
        print(f"Nenhum usuário inscrito em '{db_field.city}' para notificar via push.")
        
    return db_match

@app.get("/matches/", response_model=List[MatchOut], tags=["Matches"])
def read_matches(db: Session = Depends(get_db)):
    return db.query(Match).all()

@app.post("/matches/{match_id}/join", tags=["Matches"])
def join_match(match_id: int, user_id: int, db: Session = Depends(get_db)):
    db_match = db.query(Match).filter(Match.id == match_id).first()
    if not db_match: raise HTTPException(status_code=404, detail="Partida não encontrada")
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user: raise HTTPException(status_code=404, detail="Usuário não encontrado")
    existing_player = db.query(PlayerMatch).filter(PlayerMatch.match_id == match_id, PlayerMatch.user_id == user_id).first()
    if existing_player: raise HTTPException(status_code=400, detail="Usuário já está na partida")
    new_player_in_match = PlayerMatch(match_id=match_id, user_id=user_id)
    db.add(new_player_in_match)
    db.commit()
    
    match_topic = f"{MQTT_TOPIC_MATCH_BASE}/{match_id}/updates"
    payload = {"event": "player_joined", "data": { "user_id": db_user.id, "user_name": db_user.name, "joined_at": datetime.now().isoformat() }}
    mqtt_client.publish(match_topic, json.dumps(payload, default=str), qos=2)
    print(f"Publicado em '{match_topic}'")

    return {"message": f"Usuário '{db_user.name}' entrou na partida '{db_match.title}'"}
