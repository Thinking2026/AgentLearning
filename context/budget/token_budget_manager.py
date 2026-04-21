from __future__ import annotations

from abc import ABC, abstractmethod

from config.config import JsonConfig
from schemas.types import BudgetResult, RoleBudget
from schemas.errors import ConfigError


_DEFAULT_ROLE_RATIOS: dict[str, float] = {
    "system":    0.15,
    "user":      0.35,
    "assistant": 0.30,
    "tool":      0.20,
}
_DEFAULT_RESERVE_RATIO = 0.20


class BaseTokenBudgetManager(ABC):
    @abstractmethod
    def allocate(self, total_budget: int) -> BudgetResult: ...


class ReActTokenBudgetManager(BaseTokenBudgetManager):
    def __init__(self, config: JsonConfig) -> None:
        self._strategy_name = "react"

        self._reserve_ratio: float = config.get(
            f"token_budget.{self._strategy_name}.reserve_ratio", _DEFAULT_RESERVE_RATIO
        )
        raw_ratios: dict[str, float] = config.get(
            f"token_budget.{self._strategy_name}.role_ratios", _DEFAULT_ROLE_RATIOS
        )
        self._role_ratios = {str(k): float(v) for k, v in raw_ratios.items()}
        self._validate()

    def _validate(self) -> None:
        if not (0.0 < self._reserve_ratio < 1.0):
            raise ConfigError(
                f"token_budget.{self._strategy_name}.reserve_ratio must be in (0, 1), "
                f"got {self._reserve_ratio}"
            )
        total = sum(self._role_ratios.values())
        if abs(total - 1.0) > 1e-6:
            raise ConfigError(
                f"token_budget.{self._strategy_name}.role_ratios must sum to 1.0, "
                f"got {total:.6f}"
            )

    def allocate(self, total_budget: int) -> BudgetResult:
        reserved = int(total_budget * self._reserve_ratio)
        available = total_budget - reserved

        role_budgets = {
            role: RoleBudget(
                role=role,
                ratio=ratio,
                token_budget=int(available * ratio),
            )
            for role, ratio in self._role_ratios.items()
        }

        return BudgetResult(
            strategy=self._strategy_name,
            total_budget=total_budget,
            reserve_ratio=self._reserve_ratio,
            reserved_tokens=reserved,
            available_tokens=available,
            role_budgets=role_budgets,
        )


class TokenBudgetManagerFactory:
    """Creates a budget manager keyed by the agent's reasoning strategy name.

    Config example:
        {
          "token_budget": {
            "react": {
              "reserve_ratio": 0.20,
              "role_ratios": { "system": 0.15, "user": 0.35,
                               "assistant": 0.30, "tool": 0.20 }
            }
          }
        }
    """

    @classmethod
    def create(cls, strategy_name: str, config: JsonConfig) -> BaseTokenBudgetManager:
        if strategy_name == "react":
            return ReActTokenBudgetManager(config)
        raise ConfigError(f"Unsupported strategy for token budget manager: {strategy_name}") 
