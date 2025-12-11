from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()

# --- NIVEL 1: EL CLIENTE SAAS (EL CLUB) ---
class Club(Base):
    __tablename__ = "clubs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    admin_phone = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    players = relationship("Player", back_populates="club")
    tournaments = relationship("Tournament", back_populates="club")

# --- NIVEL 2: LOS USUARIOS DE WHATSAPP ---
class WhatsAppUser(Base):
    __tablename__ = "whatsapp_users"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    players = relationship("Player", back_populates="owner")

# --- NIVEL 3: LOS JUGADORES ---
class Player(Base):
    __tablename__ = "players"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    elo = Column(Integer, default=1200)
    
    # --- NUEVO: CATEGORÍA ---
    # Ej: "Primera", "Segunda", "Damas", "Senior"
    category = Column(String, default="General") 
    
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    avatar_url = Column(String, nullable=True)
    
    club_id = Column(Integer, ForeignKey("clubs.id"))
    club = relationship("Club", back_populates="players")
    
    owner_id = Column(Integer, ForeignKey("whatsapp_users.id"))
    owner = relationship("WhatsAppUser", back_populates="players")

# --- NIVEL 4: EL TORNEO ---
class Tournament(Base):
    __tablename__ = "tournaments"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    status = Column(String, default="inscription")
    
    # --- NUEVO: CATEGORÍA DEL TORNEO ---
    # Un torneo puede ser solo para "Primera" o "General"
    category = Column(String, default="General")
    
    smart_data = Column(JSON, default={}) 
    created_at = Column(DateTime, default=datetime.utcnow)
    
    club_id = Column(Integer, ForeignKey("clubs.id"))
    club = relationship("Club", back_populates="tournaments")
    
    matches = relationship("Match", back_populates="tournament")

# --- NIVEL 5: LOS PARTIDOS ---
class Match(Base):
    __tablename__ = "matches"
    
    id = Column(Integer, primary_key=True, index=True)
    
    player_1_id = Column(Integer, ForeignKey("players.id"))
    player_2_id = Column(Integer, ForeignKey("players.id"))
    winner_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    
    score = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_finished = Column(Boolean, default=True)
    
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), nullable=True)
    tournament = relationship("Tournament", back_populates="matches")