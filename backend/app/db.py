from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(
            url, pool_pre_ping=True, pool_size=8, max_overflow=8, hide_parameters=True
        )
        self.session = sessionmaker(self.engine, expire_on_commit=False)
