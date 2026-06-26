from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional


class CategoryBase(BaseModel):
    """Base schema with common attributes"""
    scraped_id: str = Field(..., description="ID from the source organization", min_length=1)
    organization_id: str = Field(..., description="Which organization this category came from", min_length=1)
    name: str = Field(..., description="Category name", min_length=1, max_length=100)
    description: Optional[str] = Field(None, description="Category description", max_length=1500)

    @field_validator('name', mode='before')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure name is not empty"""
        if not v or not v.strip():
            raise ValueError('Name cannot be empty')
        return v.strip()


class CategoryCreate(CategoryBase):
    """Schema for creating a new category"""
    pass


class CategoryUpdate(BaseModel):
    """Schema for updating an existing category"""
    name: Optional[str] = Field(None, description="Category name", min_length=1, max_length=100)
    description: Optional[str] = Field(None, description="Category description", max_length=1500)
    is_active: Optional[bool] = Field(None, description="Whether the category is active")

    @field_validator('name', mode='before')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not v or not v.strip():
                raise ValueError('Name cannot be empty')
            return v.strip()
        return v


class CategoryResponse(CategoryBase):
    """Schema for API responses"""
    id: str = Field(..., description="Internal UUID")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CategoryWithProjectCount(CategoryResponse):
    """Category response with project count"""
    project_count: int = Field(0, description="Number of projects in this category")

    class Config:
        from_attributes = True


class CategoryListResponse(BaseModel):
    """Schema for paginated category list"""
    items: list[CategoryResponse]
    total: int = Field(..., description="Total number of categories")
    page: int = Field(1, description="Current page number")
    per_page: int = Field(100, description="Items per page")
    pages: int = Field(..., description="Total number of pages")


class CategoryBulkCreate(BaseModel):
    """Schema for creating multiple categories at once"""
    categories: list[CategoryCreate]

    @field_validator('categories', mode='before')
    @classmethod
    def validate_categories(cls, v: list) -> list:
        if not v:
            raise ValueError('Categories list cannot be empty')
        return v


class CategoryBulkResponse(BaseModel):
    """Schema for bulk operation response"""
    created: int = Field(..., description="Number of categories created")
    updated: int = Field(..., description="Number of categories updated")
    failed: int = Field(..., description="Number of categories that failed")
    errors: list[dict] = Field(default_factory=list, description="Error details")