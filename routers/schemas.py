from pydantic import BaseModel, Field


class UpdateSettingsForm(BaseModel):
    enable_help: bool | None = Field(None)
    enable_random: bool | None = Field(None)
    enable_fruit: bool | None = Field(None)