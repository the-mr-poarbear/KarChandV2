from pydantic import BaseModel, Field


class FreelancerBase(BaseModel):
    scraped_id: str = Field(..., min_length=1)
    category_id: str = Field(..., min_length=1)


class FreelancerCreate(FreelancerBase):
    pass


class FreelancerResponse(FreelancerBase):
    id: str

    class Config:
        from_attributes = True