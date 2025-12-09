from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()

# --- NIVEL 1: EL CLIENTE SAAS (EL CLUB) ---
class Club(Base):
    __tablename__ = "clubs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)  # Ej: "Club Campestre"
    admin_phone = Column(String, unique=True, index=True) # El número del dueño que te paga
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Un club tiene muchos jugadores y muchos torneos
    players = relationship("Player", back_populates="club")
    tournaments = relationship("Tournament", back_populates="club")

# --- NIVEL 2: LOS USUARIOS DE WHATSAPP (LA CUENTA NETFLIX) ---
class WhatsAppUser(Base):
    __tablename__ = "whatsapp_users"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Un celular puede controlar varios perfiles de jugadores
    players = relationship("Player", back_populates="owner")

# --- NIVEL 3: LOS JUGADORES (PERFILES) ---
class Player(Base):
    __tablename__ = "players"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    elo = Column(Integer, default=1200)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    avatar_url = Column(String, nullable=True)
    
    # RELACIONES SAAS:
    # 1. Pertenece a un Club (SaaS)
    club_id = Column(Integer, ForeignKey("clubs.id"))
    club = relationship("Club", back_populates="players")
    
    # 2. Es controlado por un Celular (Netflix Style)
    owner_id = Column(Integer, ForeignKey("whatsapp_users.id"))
    owner = relationship("WhatsAppUser", back_populates="players")

# --- NIVEL 4: EL TORNEO (LA INTELIGENCIA DEL EVENTO) ---
class Tournament(Base):
    __tablename__ = "tournaments"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String) # Ej: "Copa Navidad"
    status = Column(String, default="inscription") # inscription, active, finished
    
    # AQUÍ ESTÁ LA MAGIA 2030:
    # En lugar de tablas rígidas, guardamos toda la estructura (llaves, horarios)
    # en un formato flexible que la IA puede leer y modificar.
    smart_data = Column(JSON, default={}) 
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Pertenece a un Club
    club_id = Column(Integer, ForeignKey("clubs.id"))
    club = relationship("Club", back_populates="tournaments")
    
    # Tiene partidos asociados
    matches = relationship("Match", back_populates="tournament")

# --- NIVEL 5: LOS PARTIDOS (HISTORIAL Y TORNEO) ---
class Match(Base):
    __tablename__ = "matches"
    
    id = Column(Integer, primary_key=True, index=True)
    
    player_1_id = Column(Integer, ForeignKey("players.id"))
    player_2_id = Column(Integer, ForeignKey("players.id"))
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    
    score = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_finished = Column(Boolean, default=True)
    
    # Opcional: Si el partido es parte de un torneo
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=True)
    tournament = relationship("Tournament", back_populates="matches")