import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 1. Obtener la dirección de la base de datos (Nube o Local)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./club_squash.db")

# 2. Ajuste técnico para Render (PostgreSQL)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL)
else:
    # Configuración para SQLite (Local)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# 3. Crear el generador de sesiones
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Función para que los otros archivos pidan la base de datos
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()