from pydantic import BaseModel, Field


class Section(BaseModel):
    name: str = "unknown"
    text: str = ""
    images: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
