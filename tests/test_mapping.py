from src.domain.mapping import MappingConfig


def test_default_profile_exists() -> None:
    config = MappingConfig.model_validate(
        {
            "default_profile": "lyre",
            "profiles": {"lyre": {"note_to_key": {"60": "a"}}},
        }
    )
    assert config.default_profile == "lyre"
    assert "lyre" in config.profiles
