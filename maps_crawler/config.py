import os


class ConfigError(RuntimeError):
    pass


def get_api_key(
    cli_key: str | None = None,
    hint: str | None = None,
    env_var: str = "GOOGLE_MAPS_API_KEY",
) -> str:
    key = cli_key or os.environ.get(env_var)
    if not key:
        message = f"No API key found. Set the {env_var} environment variable or pass --api-key."
        if hint:
            message += f" {hint}"
        raise ConfigError(message)
    return key
