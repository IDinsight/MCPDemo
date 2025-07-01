"""This module contains Pydantic models for tools."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


class WeatherData(BaseModel):
    """Pydantic model for structured weather data."""

    city: str
    condition: str
    humidity: float = Field(..., description="Humidity percentage.")
    temperature: float = Field(..., description="Temperature in Celsius.")
    wind_speed: float

    model_config = ConfigDict(from_attributes=True)
