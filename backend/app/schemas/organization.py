from enum import Enum
from pydantic import BaseModel, Field


class OrganizationName(str, Enum):
    """
    Known source organizations that can be scraped.

    The enum value must match the `name` column on the Organization row
    exactly (case-sensitive) - this is how we resolve enum -> organization_id.
    """
    KARLANCER = "Karlancer"
    PONISHA = "Ponisha"


class OrganizationResponse(BaseModel):
    """Schema for API responses"""
    id: str = Field(..., description="Internal UUID")
    name: str = Field(..., description="Organization name")

    class Config:
        from_attributes = True