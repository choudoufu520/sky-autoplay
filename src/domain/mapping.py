from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MappingProfile(BaseModel):
    note_to_key: dict[str, str] = Field(default_factory=dict)
    transpose_semitones: int = 0
    octave_shift: int = 0

    @field_validator("note_to_key")
    @classmethod
    def validate_note_key_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        for note, key in value.items():
            if not str(note).strip():
                raise ValueError("note name/number cannot be empty")
            if not str(key).strip():
                raise ValueError("mapped key cannot be empty")
        return value


class MappingConfig(BaseModel):
    default_profile: str
    profiles: dict[str, MappingProfile] = Field(default_factory=dict)
    program_to_profile: dict[int, str] = Field(default_factory=dict)

    @field_validator("default_profile")
    @classmethod
    def default_profile_must_exist(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("default_profile cannot be empty")
        return value
