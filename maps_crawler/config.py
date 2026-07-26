import os


class ConfigError(RuntimeError):
    pass


def get_api_key(cli_key: str | None = None, hint: str | None = None) -> str:
    key = cli_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        message = "No API key found. Set the GOOGLE_MAPS_API_KEY environment variable or pass --api-key."
        if hint:
            message += f" {hint}"
        raise ConfigError(message)
    return key
