"""
RoadFit-X: Vehicle Digital Twin
Defines a comprehensive physical-operational profile for routing vehicles.
"""
from typing import List, Literal
from pydantic import BaseModel, Field, ConfigDict

class VehicleDigitalTwin(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    vehicle_type: str
    width_m: float = Field(gt=0, allow_inf_nan=False)
    height_m: float = Field(gt=0, allow_inf_nan=False)
    gross_weight_t: float = Field(gt=0, allow_inf_nan=False)
    axle_load_t: float = Field(gt=0, allow_inf_nan=False)
    wheelbase_m: float = Field(gt=0, allow_inf_nan=False)
    turning_radius_m: float = Field(gt=0, allow_inf_nan=False)
    ground_clearance_m: float = Field(gt=0, allow_inf_nan=False)
    max_grade_pct: float = Field(gt=0, allow_inf_nan=False)
    width_buffer_m: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    height_buffer_m: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    surface_tolerance: List[str]
    rain_tolerance: Literal["low", "medium", "high"]
    risk_preference: Literal["aggressive", "moderate", "conservative"]
    unknown_data_policy: Literal["strict", "conservative", "exploratory"] = "conservative"

    def __str__(self):
        return f"{self.vehicle_type} (W:{self.width_m}m, H:{self.height_m}m, Wgt:{self.gross_weight_t}t)"
