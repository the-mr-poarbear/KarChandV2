from pydantic import BaseModel, Field


class SkillBase(BaseModel):
    scraped_id: str = Field(..., min_length=1)
    organization_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)


class SkillCreate(SkillBase):
    pass


class SkillResponse(SkillBase):
    id: str

    class Config:
        from_attributes = True