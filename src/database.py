import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Table, ForeignKey
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


# Junction table for video-compilation relationship
compilation_videos = Table(
    'compilation_videos',
    Base.metadata,
    Column('compilation_id', Integer, ForeignKey('compilations.id')),
    Column('video_id', Integer, ForeignKey('videos.id')),
    Column('added_at', DateTime, default=lambda: datetime.now(timezone.utc)),
)


class Video(Base):
    __tablename__ = 'videos'

    id = Column(Integer, primary_key=True)
    youtube_id = Column(String(20), unique=True, nullable=False, index=True)
    title = Column(String(500))
    url = Column(String(500))
    duration = Column(Integer)  # seconds
    upload_date = Column(String(10))  # YYYY-MM-DD
    view_count = Column(Integer)  # YouTube view count
    channel = Column(String(200))
    topic = Column(String(100), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    compilations = relationship('Compilation', secondary=compilation_videos, back_populates='videos')


class Compilation(Base):
    __tablename__ = 'compilations'

    id = Column(Integer, primary_key=True)
    topic = Column(String(100))
    filename = Column(String(500))
    video_count = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    videos = relationship('Video', secondary=compilation_videos, back_populates='compilations')


class Database:

    def __init__(self, db_path: str = "data/videos.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.engine = create_engine(f'sqlite:///{db_path}')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.Session()

    @contextmanager
    def session_scope(self):
        """Context manager that commits on success, rolls back on error."""
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
