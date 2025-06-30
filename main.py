import os
import time
import json
import enum
import asyncio
from datetime import date, time as time_type, datetime, timedelta
from typing import List, Optional, Any # Adicione Any para o JSON
import firebase_admin
from firebase_admin import credentials, messaging
import paho.mqtt.client as paho
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, ConfigDict, computed_field, model_validator, field_validator
from sqlalchemy import (create_engine, Column, Integer, String, DateTime, ForeignKey,
                        DECIMAL, Date, Time, Boolean, func, Enum as SQLAlchemyEnum, UniqueConstraint,
                        JSON) # <-- IMPORTE O TIPO JSON
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base, selectinload, joinedload
from sqlalchemy.ext.hybrid import hybrid_property
from passlib.context import CryptContext
from jose import JWTError, jwt
from dotenv import load_dotenv


# --- 1. CONFIGURAÇÕES E INICIALIZAÇÕES ---
load_dotenv()

# Firebase Admin SDK Setup
firebase_initialized = False
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CRED_FILENAME = os.getenv("FIREBASE_CREDENTIALS_FILENAME", "futside-d414e-firebase-adminsdk-fbsvc-b53b08bd01.json")
    cred_path = os.path.join(BASE_DIR, CRED_FILENAME)
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
            firebase_initialized = True
            print("✅ Firebase Admin SDK inicializado com sucesso")
    else:
        print(f"❌ Arquivo de credenciais Firebase não encontrado: {cred_path}")
except Exception as e:
    print(f"❌ ERRO ao inicializar o Firebase Admin: {e}")
    firebase_initialized = False

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL: 
    raise ValueError("DATABASE_URL não definida no arquivo .env!")

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

SECRET_KEY = os.getenv("SECRET_KEY", "a_very_secret_key_that_should_be_in_env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# --- 2. ENUMS ---
class SkillLevelEnum(str, enum.Enum): 
    beginner="beginner"
    intermediate="intermediate"
    advanced="advanced"
    professional="professional"

class MatchStatusEnum(str, enum.Enum):
    scheduled = "scheduled"
    confirmed = "confirmed"
    in_progress = "in_progress" # <-- Adicionar este
    canceled = "canceled"
    completed = "completed"

# --- 3. SCHEMAS PYDANTIC ---
class ConfigBase:
    from_attributes = True

class HourDetail(BaseModel):
    day: str
    time: str

class Token(BaseModel): 
    access_token: str
    token_type: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel): 
    name: str
    email: EmailStr
    password: str
    phone: Optional[str] = None
    city: Optional[str] = None

class UserOut(BaseModel): 
    id: int
    name: str
    email: EmailStr
    model_config = ConfigDict(from_attributes=True)

class FieldCreate(BaseModel): 
    name: str
    address: str
    city: str
    state: str
    title: Optional[str] = None # <-- NOVO
    description: Optional[str] = None # <-- NOVO
    price: Optional[str] = None # <-- NOVO
    phone: Optional[str] = None # <-- NOVO
    email: Optional[EmailStr] = None # <-- NOVO
    images: Optional[List[str]] = [] # <-- NOVO (lista de URLs)
    hours: Optional[List[dict]] = [] 
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class UserSubscriptionOut(BaseModel):
    subscribed_cities: List[str]

class FieldOut(FieldCreate): 
    id: int
    locador_id: int
    rating: Optional[float] = 0
    reviews: Optional[int] = 0
    
    model_config = ConfigDict(from_attributes=True) # Configuração simples é suficiente

    @field_validator('images', 'hours', mode='before')
    @classmethod
    def empty_list_if_none(cls, v: Any) -> Any:
        """
        Esta é a solução definitiva: se o valor vindo do banco de dados (antes da validação)
        for None, nós o transformamos em uma lista vazia.
        """
        return v if v is not None else []

class PlayerProfileUpdate(BaseModel): 
    position: str
    skill_level: SkillLevelEnum

class PlayerProfileOut(PlayerProfileUpdate): 
    user_id: int
    model_config = ConfigDict(from_attributes=True)

class MatchCreate(BaseModel): 
    field_id: int
    title: str
    description: Optional[str] = None
    date: date
    start_time: time_type
    end_time: time_type
    max_players: int

class FieldNestedOut(BaseModel):
    name: str
    city: str
    model_config = ConfigDict(from_attributes=True)

class PlayerInMatch(UserOut): 
    pass

class MatchOut(MatchCreate): 
    id: int
    creator_id: int
    status: MatchStatusEnum
    field: FieldNestedOut
    player_count: int
    score_a: int  # <-- ADICIONE ESTA LINHA
    score_b: int  # <-- ADICIONE ESTA LINHA
    model_config = ConfigDict(from_attributes=True)

class PublicUserProfileOut(UserOut): 
    player_profile: Optional[PlayerProfileOut] = None
    model_config = ConfigDict(from_attributes=True)

class MatchStartResponse(BaseModel):
    message: str
    match_id: int

class ScoreUpdateRequest(BaseModel):
    score_a: int
    score_b: int


class TokenRegistration(BaseModel): 
    fcm_token: str

class RegionSubscription(BaseModel): 
    city: str

class MatchDetailOut(MatchOut): 
    players: List[PlayerInMatch] = []
    # score_a: int  <-- REMOVA ESTA LINHA
    # score_b: int  <-- REMOVA ESTA LINHA
    model_config = ConfigDict(from_attributes=True)

# --- 4. MODELOS SQLALCHEMY ---
class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    phone = Column(String, nullable=True)
    fcm_token = Column(String, nullable=True, index=True)
    
    locador = relationship("Locador", back_populates="user", uselist=False, cascade="all, delete-orphan")
    player_profile = relationship("UserPlayerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    matches_created = relationship("Match", back_populates="creator")
    matches_joined = relationship("PlayerMatch", back_populates="user")

class Locador(Base): 
    __tablename__ = "locador"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), unique=True)
    
    user = relationship("User", back_populates="locador")
    fields = relationship("Field", back_populates="locador", cascade="all, delete-orphan")

class Field(Base): 
    __tablename__ = "field"
    id = Column(Integer, primary_key=True)
    locador_id = Column(Integer, ForeignKey("locador.id"))
    name = Column(String)
    address = Column(String)
    city = Column(String, index=True)
    state = Column(String)
    latitude = Column(DECIMAL, nullable=True)
    longitude = Column(DECIMAL, nullable=True)
    
    # --- NOVOS CAMPOS ---
    title = Column(String, nullable=True)
    description = Column(String, nullable=True)
    price = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    images = Column(JSON, nullable=True) # Armazena uma lista de strings (URLs)
    hours = Column(JSON, nullable=True) # Armazena uma lista de objetos {day, time}
    # rating e reviews poderiam ser colunas aqui ou calculadas a partir de outra tabela
    
    locador = relationship("Locador", back_populates="fields")
    matches = relationship("Match", back_populates="field")

class Match(Base): 
    __tablename__ = "match"
    id = Column(Integer, primary_key=True)
    field_id = Column(Integer, ForeignKey("field.id"))
    creator_id = Column(Integer, ForeignKey("user.id"))
    title = Column(String)
    description = Column(String, nullable=True)
    date = Column(Date)
    start_time = Column(Time)
    end_time = Column(Time)
    max_players = Column(Integer)
    status = Column(SQLAlchemyEnum(MatchStatusEnum), default=MatchStatusEnum.scheduled)
    score_a = Column(Integer, default=0)
    score_b = Column(Integer, default=0)
    live_details = Column(JSON, nullable=True) 
    field = relationship("Field", back_populates="matches")
    creator = relationship("User", back_populates="matches_created")
    players = relationship("PlayerMatch", back_populates="match", cascade="all, delete-orphan")
    
    @hybrid_property
    def player_count(self):
        return len(self.players)

class PlayerMatch(Base): 
    __tablename__ = "player_match"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("match.id"))
    user_id = Column(Integer, ForeignKey("user.id"))
    
    match = relationship("Match", back_populates="players")
    user = relationship("User", back_populates="matches_joined")


class UserPlayerProfile(Base): 
    __tablename__ = "user_player_profile"
    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True)
    position = Column(String, nullable=True)
    skill_level = Column(SQLAlchemyEnum(SkillLevelEnum), nullable=True)
    
    user = relationship("User", back_populates="player_profile")

class UserRegionSubscription(Base): 
    __tablename__ = "user_region_subscription"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"))
    city = Column(String, index=True)
    
    __table_args__ = (UniqueConstraint('user_id', 'city'),)

Base.metadata.create_all(bind=engine)

# --- 5. FUNÇÕES DE UTILIDADE E DEPENDÊNCIAS ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password): 
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password): 
    return pwd_context.hash(password)

def get_db(): 
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict): 
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None: 
            raise credentials_exception
    except JWTError: 
        raise credentials_exception
    
    user = db.query(User).filter(User.email == email).first()
    if user is None: 
        raise credentials_exception
    return user

def _subscribe_user_to_default_region(db: Session, user: User):
    """
    Inscreve um usuário na região padrão ('Asa Sul') se ele ainda não estiver inscrito.
    """
    default_city = "brasilia"
    
    # Verifica se a inscrição já existe para não causar um erro de constraint
    existing_sub = db.query(UserRegionSubscription).filter_by(
        user_id=user.id,
        city=default_city
    ).first()
    
    if not existing_sub:
        # Se não existe, cria a nova inscrição
        new_sub = UserRegionSubscription(user_id=user.id, city=default_city)
        db.add(new_sub)
        print(f"✅ Usuário {user.name} inscrito automaticamente em {default_city}.")
        # O commit será feito pela função que chamou esta.


# FUNÇÃO MELHORADA PARA NOTIFICAÇÕES FCM
async def send_batch_push_notifications(tokens: List[str], title: str, body: str, data: Optional[dict] = None):
    """
    Envia notificações push usando Firebase Cloud Messaging de forma assíncrona
    """
    if not firebase_initialized:
        print("❌ Firebase não inicializado. Pulando notificações push.")
        return False
    
    # Filtra tokens válidos
    valid_tokens = [token.strip() for token in tokens if token and token.strip()]
    if not valid_tokens:
        print("❌ Nenhum token FCM válido encontrado.")
        return False
    
    # Cria a mensagem multicast
    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        data=data or {},
        tokens=valid_tokens,
        android=messaging.AndroidConfig(
            notification=messaging.AndroidNotification(
                channel_id="default",
                priority="high"
            )
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(
                        title=title,
                        body=body
                    ),
                    badge=1,
                    sound="default"
                )
            )
        )
    )
    
    try:
        # Envia as notificações
        response = messaging.send_each_for_multicast(message)
        
        print(f'✅ Notificações FCM enviadas:')
        print(f'   - Sucessos: {response.success_count}')
        print(f'   - Falhas: {response.failure_count}')
        
        # Log dos erros se houver
        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    print(f'   - Erro no token {idx}: {resp.exception}')
        
        return response.success_count > 0
        
    except Exception as e:
        print(f'❌ Erro ao enviar notificações FCM: {e}')
        return False

# --- 6. CONFIGURAÇÃO MQTT MELHORADA ---
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_TOPIC_REGIONAL_BASE = "futside/matches"
MQTT_TOPIC_MATCH_BASE = "futside/match"

# Cliente MQTT Global
mqtt_client = None
mqtt_connected = False

def setup_mqtt_client():
    global mqtt_client, mqtt_connected
    
    mqtt_client = paho.Client(client_id=f"fastapi_publisher_{int(time.time())}")
    
    def on_connect(client, userdata, flags, rc, properties=None):
        global mqtt_connected
        if rc == 0:
            mqtt_connected = True
            print(f"✅ Conectado ao Broker MQTT em {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        else:
            mqtt_connected = False
            print(f"❌ Falha ao conectar ao Broker MQTT, código: {rc}")
    
    def on_disconnect(client, userdata, rc):
        global mqtt_connected
        mqtt_connected = False
        print(f"🔌 Desconectado do Broker MQTT (código: {rc})")
    
    def on_publish(client, userdata, mid):
        print(f"📤 Mensagem MQTT publicada (mid: {mid})")
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_publish = on_publish
    
    try:
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_start()
        return True
    except Exception as e:
        print(f"❌ ERRO: Não foi possível conectar ao Broker MQTT: {e}")
        mqtt_connected = False
        return False

def publish_mqtt_message(topic: str, payload: dict):
    """
    Publica uma mensagem MQTT de forma segura
    """
    global mqtt_client, mqtt_connected
    
    if not mqtt_connected or not mqtt_client:
        print(f"❌ MQTT não conectado. Não foi possível publicar em {topic}")
        return False
    
    try:
        message_json = json.dumps(payload, default=str)
        result = mqtt_client.publish(topic, message_json, qos=1)
        
        if result.rc == paho.MQTT_ERR_SUCCESS:
            print(f"✅ Mensagem MQTT publicada com sucesso em {topic}")
            return True
        else:
            print(f"❌ Falha ao publicar mensagem MQTT em {topic} (código: {result.rc})")
            return False
            
    except Exception as e:
        print(f"❌ Erro ao publicar mensagem MQTT: {e}")
        return False

# Inicializa o cliente MQTT
setup_mqtt_client()

# --- 7. INICIALIZAÇÃO DA API ---
app = FastAPI(title="Futside API v.Complete - Fixed")

@app.on_event("shutdown")
def shutdown_event():
    global mqtt_client
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("🔌 Cliente MQTT desconectado na finalização da aplicação")

# --- 8. ROTAS DA API ---
@app.post("/matches/{match_id}/start", response_model=MatchStartResponse, tags=["Matches & Feed"])
def start_match(match_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_match = db.query(Match).filter(Match.id == match_id).first()
    if not db_match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    if db_match.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Apenas o criador pode iniciar a partida.")

    # Altera o status da partida
    db_match.status = MatchStatusEnum.in_progress
    db.commit()

    # Publica uma mensagem no tópico do LOBBY para redirecionar todos
    lobby_topic = f"{MQTT_TOPIC_MATCH_BASE}/{match_id}/updates"
    mqtt_payload = {"event": "match_started", "data": {"match_id": match_id}}
    publish_mqtt_message(lobby_topic, mqtt_payload)

    return {"message": "Partida iniciada com sucesso", "match_id": match_id}


@app.put("/matches/{match_id}/score", status_code=status.HTTP_204_NO_CONTENT, tags=["Matches & Feed"])
def update_score(match_id: int, payload: ScoreUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_match = db.query(Match).filter(Match.id == match_id).first()
    if not db_match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    if db_match.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Apenas o criador pode alterar o placar.")

    # Atualiza o placar no banco
    db_match.score_a = payload.score_a
    db_match.score_b = payload.score_b
    db.commit()

    # Publica a atualização do placar no tópico da PARTIDA AO VIVO
    live_topic = f"{MQTT_TOPIC_MATCH_BASE}/{match_id}/live_updates"
    mqtt_payload = {"event": "score_update", "data": payload.model_dump()}
    publish_mqtt_message(live_topic, mqtt_payload)

    return

@app.post("/auth/token", response_model=Token, tags=["Authentication"])
async def login_for_access_token(form_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.email).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos"
        )
    
    _subscribe_user_to_default_region(db=db, user=user)
    db.commit() 
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/users/", response_model=Token, tags=["Users & Profiles"])
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email já registado")
    
    new_user = User(
        name=user.name,
        email=user.email,
        hashed_password=get_password_hash(user.password),
        phone=user.phone
    )
    db.add(new_user)
    db.flush()
    
    db.add(Locador(user_id=new_user.id))
    
    _subscribe_user_to_default_region(db=db, user=new_user)

    
    db.commit()
    access_token = create_access_token(data={"sub": new_user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=PublicUserProfileOut, tags=["Users & Profiles"])
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/users/{user_id}", response_model=PublicUserProfileOut, tags=["Users & Profiles"])
def read_user_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    return user

@app.delete("/users/me/subscriptions/region/{city}", tags=["Notifications"])
def unsubscribe_from_region(
    city: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Encontra a inscrição existente
    existing_sub = db.query(UserRegionSubscription).filter_by(
        user_id=current_user.id,
        city=city
    ).first()
    
    if not existing_sub:
        # Se não existir, não há o que fazer, mas podemos retornar sucesso
        return {"message": "Utilizador não estava inscrito nesta região."}
    
    # Deleta a inscrição do banco de dados
    db.delete(existing_sub)
    db.commit()
    return {"message": f"Inscrição para {city} removida com sucesso."}


# ROTA 2: Adicionar uma rota para CONSULTAR as inscrições do usuário
@app.get("/users/me/subscriptions", response_model=UserSubscriptionOut, tags=["Notifications"])
def get_my_subscriptions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    subscriptions = db.query(UserRegionSubscription.city).filter_by(user_id=current_user.id).all()
    # O resultado de .all() é uma lista de tuplas, ex: [('Asa Sul',), ('Asa Norte',)]
    # Precisamos extrair o primeiro elemento de cada tupla.
    subscribed_cities = [sub[0] for sub in subscriptions]
    return {"subscribed_cities": subscribed_cities}


@app.put("/users/me/player-profile", response_model=PlayerProfileOut, tags=["Users & Profiles"])
def create_or_update_player_profile(
    profile: PlayerProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    db_profile = db.query(UserPlayerProfile).filter(
        UserPlayerProfile.user_id == current_user.id
    ).first()
    
    if db_profile:
        db_profile.position = profile.position
        db_profile.skill_level = profile.skill_level
    else:
        db_profile = UserPlayerProfile(**profile.model_dump(), user_id=current_user.id)
        db.add(db_profile)
    
    db.commit()
    db.refresh(db_profile)
    return db_profile

@app.post("/users/me/register-fcm", tags=["Notifications"])
def register_fcm_token(
    payload: TokenRegistration,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    print(f"📱 Registrando token FCM para usuário {current_user.name}: {payload.fcm_token[:20]}...")
    db.query(User).filter( 
        User.fcm_token == payload.fcm_token,
        User.id != current_user.id
    ).update({"fcm_token": None})
    
    # 2. Atribui o token ao usuário atual.
    current_user.fcm_token = payload.fcm_token
    
    # 3. Salva as alterações no banco de dados.
    db.commit()
    return {"message": "Token FCM atualizado com sucesso"}

@app.post("/users/me/subscriptions/region", tags=["Notifications"])
def subscribe_to_region(
    payload: RegionSubscription,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    existing_sub = db.query(UserRegionSubscription).filter_by(
        user_id=current_user.id,
        city=payload.city
    ).first()
    
    if existing_sub:
        return {"message": "Utilizador já inscrito nesta região"}
    
    new_sub = UserRegionSubscription(user_id=current_user.id, city=payload.city)
    db.add(new_sub)
    db.commit()
    return {"message": f"Inscrito com sucesso em {payload.city}"}

@app.get("/fields/feed", response_model=List[FieldOut], tags=["Fields & Feed"])
def get_fields_feed(city: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Field)
    if city:
        query = query.filter(func.lower(Field.city) == func.lower(city))
    return query.order_by(Field.id.desc()).limit(50).all()

@app.get("/fields/{field_id}", response_model=FieldOut, tags=["Fields & Feed"])
def get_field_details(field_id: int, db: Session = Depends(get_db)):
    db_field = db.query(Field).filter(Field.id == field_id).first()
    if not db_field:
        raise HTTPException(status_code=404, detail="Quadra não encontrada")
    return db_field

@app.get("/fields/me", response_model=List[FieldOut], tags=["Fields & Feed"])
def get_my_fields(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    locador = db.query(Locador).filter(Locador.user_id == current_user.id).first()
    if not locador:
        return []
    return db.query(Field).filter(Field.locador_id == locador.id).all()

@app.post("/fields/", response_model=FieldOut, tags=["Fields & Feed"])
def create_field(
    field: FieldCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    locador = db.query(Locador).filter(Locador.user_id == current_user.id).first()
    if not locador:
        raise HTTPException(status_code=403, detail="Apenas locadores podem criar quadras")
    
    db_field = Field(**field.model_dump(), locador_id=locador.id)
    db.add(db_field)
    db.commit()
    db.refresh(db_field)
    return db_field

@app.get("/matches/feed", response_model=List[MatchOut], tags=["Matches & Feed"])
def get_matches_feed(city: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Match).options(
        selectinload(Match.players),
        joinedload(Match.field)
    ).join(Field)
    
    if city:
        query = query.filter(func.lower(Field.city) == func.lower(city))
    
    return query.filter(Match.date >= date.today()).order_by(
        Match.date, Match.start_time
    ).limit(100).all()

@app.get("/matches/{match_id}", response_model=MatchDetailOut, tags=["Matches & Feed"])
def get_match_details(match_id: int, db: Session = Depends(get_db)):
    db_match = db.query(Match).options(
        selectinload(Match.players).selectinload(PlayerMatch.user),
        joinedload(Match.field)
    ).filter(Match.id == match_id).first()
    
    if not db_match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
        
    players_list = [pm.user for pm in db_match.players]
    match_data = MatchOut.model_validate(db_match)
    response = MatchDetailOut(
        **match_data.model_dump(),
        players=players_list
    )

    return response

@app.post("/matches/", response_model=MatchOut, tags=["Matches & Feed"])
async def create_match(
    match: MatchCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Verifica se a quadra existe
    db_field = db.query(Field).filter(Field.id == match.field_id).first()
    if not db_field:
        raise HTTPException(
            status_code=404,
            detail=f"Quadra com id {match.field_id} não encontrada"
        )
    
    # Cria a partida
    db_match = Match(**match.model_dump(), creator_id=current_user.id)
    db.add(db_match)
    db.commit()
    db.refresh(db_match)
    
    # Recarrega com relacionamentos
    db.refresh(db_match, attribute_names=['players', 'field'])
    
    # Prepara dados para MQTT e notificações
    city_normalized = db_field.city.lower().replace(' ', '-')
    match_data = MatchOut.model_validate(db_match)
    
    # MQTT - Notificação regional
    regional_topic = f"{MQTT_TOPIC_REGIONAL_BASE}/{city_normalized}"
    mqtt_payload = {
        "event": "new_match",
        "data": match_data.model_dump()
    }
    
    publish_mqtt_message(regional_topic, mqtt_payload)
    
    # FCM - Busca usuários inscritos na região
    subscriptions = db.query(UserRegionSubscription.user_id).filter(
        func.lower(UserRegionSubscription.city) == func.lower(db_field.city)
    ).all()
    
    subscribed_user_ids = {sub.user_id for sub in subscriptions}
    
    if subscribed_user_ids:
        # Busca tokens FCM válidos (excluindo o criador da partida)
        users = db.query(User.fcm_token).filter(
            User.id.in_(subscribed_user_ids),
            User.id != current_user.id,
            User.fcm_token.isnot(None)
        ).all()
        
        tokens_to_notify = [user.fcm_token for user in users if user.fcm_token]
        unique_tokens_to_notify = list(set(tokens_to_notify))
        
        if tokens_to_notify:
            print(f"📱 Enviando notificações FCM para {len(unique_tokens_to_notify)} tokens únicos")
            
            # Envia notificações em background
            background_tasks.add_task(
                send_batch_push_notifications,
                tokens=unique_tokens_to_notify, # <--- Use a lista corrigida
                title="⚽ Nova Partida na sua Área!",
                body=f"A partida '{db_match.title}' foi criada em {db_field.city}. Toque para ver!",
                data={
                    "matchId": str(db_match.id),
                    "city": db_field.city,
                    "type": "new_match"
                }
            )
        else:
            print("📱 Nenhum token FCM válido encontrado para notificações")
    else:
        print("📱 Nenhum usuário inscrito na região encontrado")
    
    return match_data

@app.post("/matches/{match_id}/join", tags=["Matches & Feed"])
async def join_match(
    match_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Busca a partida
    db_match = db.query(Match).filter(Match.id == match_id).first()
    if not db_match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    
    # Verifica se a partida não é do passado
    if db_match.date < date.today():
        raise HTTPException(status_code=400, detail="Não pode entrar em partidas passadas")
    
    # Verifica se o jogador já está na partida
    existing_player = db.query(PlayerMatch).filter(
        PlayerMatch.match_id == match_id,
        PlayerMatch.user_id == current_user.id
    ).first()
    
    if existing_player:
        raise HTTPException(status_code=400, detail="Já está na partida")
    
    # Verifica se há vagas disponíveis
    player_count = db.query(PlayerMatch).filter(PlayerMatch.match_id == match_id).count()
    if player_count >= db_match.max_players:
        raise HTTPException(status_code=400, detail="Partida cheia")
    
    # Adiciona o jogador à partida
    new_player_in_match = PlayerMatch(match_id=match_id, user_id=current_user.id)
    db.add(new_player_in_match)
    db.commit()
    
    # MQTT - Notifica sobre novo jogador no lobby
    match_topic = f"{MQTT_TOPIC_MATCH_BASE}/{match_id}/updates"
    mqtt_payload = {
        "event": "player_joined",
        "data": {
            "user_id": current_user.id,
            "user_name": current_user.name,
            "player_count": player_count + 1,
            "max_players": db_match.max_players
        }
    }
    
    publish_mqtt_message(match_topic, mqtt_payload)
    
    print(f"✅ Usuário {current_user.name} entrou na partida {db_match.title}")
    
    return {"message": f"Entrou na partida '{db_match.title}'"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)