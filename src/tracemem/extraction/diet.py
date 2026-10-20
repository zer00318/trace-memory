"""OWNS: the marketplace's constitution — the two-sided diet contract
(map: Extraction / The specialist marketplace / The diet contract. PUBLIC).

Eat-side (Diet): what a specialist can digest — declared, so the auction
can prepare and serve without the specialist ever touching the river.
Want-side (Bid): how a specialist (or a hungry node) asks for a future
gaze — its only hand on the priority map. No pull API exists, on purpose:
like cortex, a consumer can only bias the NEXT gaze.

THE BIAS LAW lives here structurally: specialists register instances of
this contract; none of them is architecture, every one is droppable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Region = Tuple[int, int]


@dataclass(frozen=True)
class Diet:
    """Eat-side: declared once at registration."""
    name: str
    continuous: bool = False    # drinks the river raw (peripheral-class only)
    min_quality: float = 0.0    # blur floor a meal must clear
    novel_only: bool = True     # refuses meals it has already been served


@dataclass(frozen=True)
class Bid:
    """Want-side: a hunger, posted to the auction."""
    bidder: str                 # specialist or node that hungers
    value: float                # how much this gaze is worth to it
    born_ms: int
    region: Optional[Region] = None   # None = wherever it appears
    context: str = ""           # what the bidder already knows / wants next


class DietRegistry:
    def __init__(self) -> None:
        self._diets: Dict[str, Diet] = {}

    def register(self, diet: Diet) -> None:
        self._diets[diet.name] = diet

    def drop(self, name: str) -> bool:
        """THE BIAS LAW: any stall must be droppable without the system
        noticing — dropping never raises."""
        return self._diets.pop(name, None) is not None

    def get(self, name: str) -> Optional[Diet]:
        return self._diets.get(name)

    def meal_eaters(self) -> List[Diet]:
        """Everyone who eats prepared meals (continuous drinkers tap the
        river upstream and never join the broadcast)."""
        return [d for d in self._diets.values() if not d.continuous]
