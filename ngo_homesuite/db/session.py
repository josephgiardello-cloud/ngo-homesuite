from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_engine = None
_Session = None

def init_engine(db_path: str):
    global _engine, _Session
    if _engine:
        return
    _engine = create_engine(f"sqlite:///{db_path}", future=True)
    _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

def get_session():
    if not _Session:
        raise RuntimeError("DB engine not initialized")
    return _Session()
