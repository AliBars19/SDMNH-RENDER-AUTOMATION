from src.database import Database, Video, Compilation, compilation_videos


class TestDatabase:

    def test_create_tables(self, db):
        session = db.get_session()
        # Tables should exist and be queryable
        assert session.query(Video).count() == 0
        assert session.query(Compilation).count() == 0
        session.close()

    def test_add_video(self, session):
        video = Video(
            youtube_id="test123",
            title="Test Video",
            url="https://youtube.com/watch?v=test123",
            duration=3600,
            upload_date="2024-01-01",
            channel="TestChannel",
            topic="general",
        )
        session.add(video)
        session.commit()

        result = session.query(Video).filter_by(youtube_id="test123").first()
        assert result is not None
        assert result.title == "Test Video"
        assert result.duration == 3600
        assert result.topic == "general"

    def test_unique_youtube_id(self, session):
        v1 = Video(youtube_id="dup123", title="First", topic="general")
        v2 = Video(youtube_id="dup123", title="Second", topic="general")
        session.add(v1)
        session.commit()
        session.add(v2)

        import sqlalchemy
        try:
            session.commit()
            assert False, "Should have raised IntegrityError"
        except sqlalchemy.exc.IntegrityError:
            session.rollback()

    def test_session_scope_commits(self, db):
        with db.session_scope() as session:
            session.add(Video(youtube_id="scope1", title="Scoped", topic="general"))

        # Verify committed
        with db.session_scope() as session:
            assert session.query(Video).filter_by(youtube_id="scope1").count() == 1

    def test_session_scope_rollback_on_error(self, db):
        try:
            with db.session_scope() as session:
                session.add(Video(youtube_id="rollback1", title="Will Rollback", topic="general"))
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # Verify rolled back
        with db.session_scope() as session:
            assert session.query(Video).filter_by(youtube_id="rollback1").count() == 0

    def test_compilation_video_relationship(self, session, sample_videos):
        comp = Compilation(topic="among_us", filename="test.mp4", video_count=2)
        session.add(comp)
        session.flush()

        for v in sample_videos[:2]:
            session.execute(
                compilation_videos.insert().values(compilation_id=comp.id, video_id=v.id)
            )
        session.commit()

        loaded = session.query(Compilation).first()
        assert loaded.video_count == 2
        assert len(loaded.videos) == 2
