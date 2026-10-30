"""The editable General fields, without marketplace, history or owner pointers."""
from pydantic import BaseModel, ConfigDict, Field


class DraftFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    description: str = Field(default='', max_length=20000)
    price: float | None = Field(default=None, ge=0, le=10000000)
    cost: float | None = Field(default=None, ge=0, le=10000000)
    quantity: int | None = Field(default=None, ge=1, le=2147483647)
    brand: str = Field(default='', max_length=200)
    condition: str = Field(default='', max_length=200)
    primaryColor: str = Field(default='', max_length=200)
    secondaryColor: str = Field(default='', max_length=200)
    size: str = Field(default='', max_length=200)
    sku: str = Field(default='', max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=30)
    zipCode: str = Field(default='', max_length=200)
    category_path: str = Field(default='', max_length=1000)
    department: str = Field(default='', max_length=200)
    weight_oz: float | None = Field(default=None, ge=0, le=1000000)
    package_dimensions_in: str = Field(default='', max_length=200)
