"""Exception types shared across rosman.

All user-facing errors should subclass RosmanError so the CLI entrypoint can
catch them and print a clean one-line message instead of a Python traceback.
"""

from __future__ import annotations


class RosmanError(Exception):
    """Base class for all expected, user-facing rosman failures."""


class ConfigError(RosmanError):
    """The rosman.yml config file is missing, malformed, or invalid."""


class DockerUnavailableError(RosmanError):
    """Could not talk to the Docker daemon."""


class ContainerError(RosmanError):
    """Something went wrong creating, starting, or inspecting a container."""
