# src/core/position_manager.py

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import pandas as pd

@dataclass
class Position:
    symbol: str
    strike: float
    option_type: str
    quantity: int
    price: float
    order_id: str
    state: str = 'open'
    entry_time: datetime = datetime.now()
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None

class PositionManager:
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.position_history = pd.DataFrame()

    def add_position(self, position: Position) -> None:
        """Add a new position"""
        self.positions[position.order_id] = position
        self._update_history(position)

    def close_position(self, order_id: str, exit_price: float) -> None:
        """Close an existing position"""
        if order_id in self.positions:
            position = self.positions[order_id]
            position.state = 'closed'
            position.exit_time = datetime.now()
            position.exit_price = exit_price
            self._update_history(position)

    def get_open_positions(self, symbol: Optional[str] = None) -> Dict[str, Position]:
        """Get all open positions, optionally filtered by symbol"""
        open_positions = {
            order_id: pos for order_id, pos in self.positions.items() 
            if pos.state == 'open' and (symbol is None or pos.symbol == symbol)
        }
        return open_positions

    def _update_history(self, position: Position) -> None:
        """Update position history"""
        position_data = {
            'symbol': position.symbol,
            'strike': position.strike,
            'option_type': position.option_type,
            'quantity': position.quantity,
            'entry_price': position.price,
            'entry_time': position.entry_time,
            'exit_price': position.exit_price,
            'exit_time': position.exit_time,
            'state': position.state,
            'order_id': position.order_id
        }
        self.position_history = pd.concat([
            self.position_history, 
            pd.DataFrame([position_data])
        ], ignore_index=True)