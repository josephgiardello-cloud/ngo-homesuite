"""Grants services package."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from ngo_homesuite.grants.services.facade import GrantsFacade


def get_grants_facade(*args: Any, **kwargs: Any):
	from ngo_homesuite.grants.services.facade import get_grants_facade as _get_grants_facade

	return _get_grants_facade(*args, **kwargs)


def __getattr__(name: str):
	if name == "GrantsFacade":
		from ngo_homesuite.grants.services.facade import GrantsFacade

		return GrantsFacade
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["GrantsFacade", "get_grants_facade"]
