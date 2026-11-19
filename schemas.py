from pydantic import BaseModel, Field
from typing import Literal

class CampaignCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=10, max_length=5000)
    campaign_type: Literal["advertisement", "survey", "app_test", "original_content", "review"] = "survey"
    budget_micro: int = Field(ge=1000)
    reward_micro: int = Field(ge=1000)
    total_slots: int = Field(ge=1, le=100000)
    platform: Literal["telegram"] = "telegram"
    proof_type: Literal["text", "url", "image_reference"] = "text"

class TaskCreate(BaseModel):
    campaign_id: int
    title: str = Field(min_length=3, max_length=180)
    instructions: str = Field(min_length=10, max_length=5000)
    proof_type: Literal["text", "url", "image_reference"] = "text"
    reward_micro: int = Field(ge=1000)

class ProofCreate(BaseModel):
    claim_id: int
    content: str = Field(min_length=2, max_length=5000)

class ReviewProof(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=1000)

class VipUpdate(BaseModel):
    days: int = Field(ge=1, le=3650)
    features: list[str] = []
