from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

# Esta es la base para crear nuestras "hojas de excel"
Base = declarative_base()

# TABLA 1: LOS DUEÑOS DEL CELULAR (Como la cuenta de Netflix)
class WhatsAppUser(Base):
    __tablename__ = "whatsapp_users"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True) # El número de WhatsApp
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relación: Un celular puede tener muchos jugadores (Hijos, Esposa, etc.)
    players = relationship("Player", back_populates="owner")

# TABLA 2: LOS JUGADORES (Los perfiles dentro de la cuenta)
class Player(Base):
    __tablename__ = "players"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)       # Nombre (ej: "Juan")
    elo = Column(Integer, default=1200)     # Puntos iniciales
    wins = Column(Integer, default=0)       # Partidos ganados
    losses = Column(Integer, default=0)     # Partidos perdidos
    avatar_url = Column(String, nullable=True) # Foto
    
    # Conexión con el dueño del celular
    owner_id = Column(Integer, ForeignKey("whatsapp_users.id"))
    owner = relationship("WhatsAppUser", back_populates="players")

# TABLA 3: LOS PARTIDOS (El historial)
class Match(Base):
    __tablename__ = "matches"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Quién jugó contra quién
    player_1_id = Column(Integer, ForeignKey("players.id"))
    player_2_id = Column(Integer, ForeignKey("players.id"))
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    
    score = Column(String) # Ej: "3-0"
    timestamp = Column(DateTime, default=datetime.utcnow) # Cuándo jugaron
    is_finished = Column(Boolean, default=True)