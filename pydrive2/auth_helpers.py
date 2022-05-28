_OLD_CLIENT_CONFIG_KEYS = frozenset(
    (
        "client_id",
        "client_secret",
        "auth_uri",
        "token_uri",
        "revoke_uri",
        "redirect_uri",
    )
)

_CLIENT_CONFIG_KEYS = frozenset(
    (
        "client_id",
        "client_secret",
        "auth_uri",
        "token_uri",
        "redirect_uris",
    )
)


def verify_client_config(client_config, with_oauth_type=True):
    """Verifies that format of the client config
    loaded from a Google-format client secrets file.
    """

    oauth_type = None
    config = client_config

    if with_oauth_type:
        if "web" in client_config:
            oauth_type = "web"
            config = config["web"]

        elif "installed" in client_config:
            oauth_type = "installed"
            config = config["installed"]
        else:
            raise ValueError(
                "Client secrets must be for a web or installed app"
            )

    # This is the older format of client config
    if _OLD_CLIENT_CONFIG_KEYS.issubset(config.keys()):
        config["redirect_uris"] = [config["redirect_uri"]]

    # by default, the redirect uri is the first in the list
    if "redirect_uri" not in config:
        config["redirect_uri"] = config["redirect_uris"][0]

    if "revoke_uri" not in config:
        config["revoke_uri"] = "https://oauth2.googleapis.com/revoke"

    if not _CLIENT_CONFIG_KEYS.issubset(config.keys()):
        raise ValueError("Client secrets is not in the correct format.")

    return oauth_type, config
