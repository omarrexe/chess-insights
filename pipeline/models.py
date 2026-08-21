from pydantic import BaseModel
from typing import List, Optional

class Move(BaseModel):
    uci: str
    san: str
    color: str
    evaluation: float
    classification: str
    time_left: Optional[int] = None

class Game(BaseModel):
    url: str
    white: str
    black: str
    result: str
    time_control: str
    time_class: str
    moves: List[Move] = []
