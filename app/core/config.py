from dataclasses import dataclass, field
import os


@dataclass
class Settings:
    app_name: str = os.getenv("APP_NAME", "Syncnesto API")
    database_url: str | None = os.getenv("DATABASE_URL")
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )


settings = Settings()
