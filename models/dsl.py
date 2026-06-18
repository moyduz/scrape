from typing import Literal
from pydantic import BaseModel, Field


class ActionItem(BaseModel):
    label: str = ""
    href: str = ""
    style: Literal["primary", "secondary", "link"] = "primary"


class SectionContent(BaseModel):
    title: str = ""
    subtitle: str = ""
    body: str = ""


class SectionMedia(BaseModel):
    images: list[str] = Field(default_factory=list)


class DSLSection(BaseModel):
    type: Literal[
        "hero", "navbar", "features", "testimonials",
        "pricing", "faq", "footer", "cta", "gallery", "text", "unknown"
    ] = "unknown"
    variant: Literal["centered", "left", "right", "grid", "list", "default"] = "default"
    content: SectionContent = Field(default_factory=SectionContent)
    actions: list[ActionItem] = Field(default_factory=list)
    media: SectionMedia = Field(default_factory=SectionMedia)


class DSLPage(BaseModel):
    sections: list[DSLSection] = Field(default_factory=list)


class DSL(BaseModel):
    page: DSLPage = Field(default_factory=DSLPage)
