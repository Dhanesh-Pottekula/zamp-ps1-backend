"""Expose the loaded settings groups."""

from app.config.env_config import ENVConfigsClass

ENVConfigs = ENVConfigsClass()

__all__ = ["ENVConfigs"]
