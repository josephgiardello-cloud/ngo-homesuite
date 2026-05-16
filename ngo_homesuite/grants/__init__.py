"""Grants bounded context package."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from ngo_homesuite.grants.facade import GrantsFacade

__all__ = ["GrantsFacade"]


def __getattr__(name: str):
	if name == "GrantsFacade":
		from ngo_homesuite.grants.facade import GrantsFacade

		return GrantsFacade
	raise AttributeError(name)
