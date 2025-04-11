# src/strategies/base_strategy.py

from abc import ABC, abstractmethod
from typing import Dict, Optional
from datetime import datetime, time
import logging
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def execute(self, market_data: Dict) -> bool:
        """Execute the strategy"""
        pass

    def is_trading_hours(self) -> bool:
        """Check if current time is within trading hours"""
        current_time = datetime.now().time()
        start_hour = self.config.get("farsell_hour", 9)
        start_min = self.config.get("farsell_min", 15)
        trading_start = time(hour=start_hour, minute=start_min)
        trading_end = time(hour=15, minute=30)
        return trading_start <= current_time <= trading_end

    def get_symbol_config(self, symbol: str) -> Dict:
        """Get multiplier and loss limits for a symbol"""
        multipliers = {
            'NIFTY': 75,
            'BANKNIFTY': 30,
            'FINNIFTY': 65,
            'MIDCPNIFTY': 50
        }
        loss_limits = {
            'NIFTY': -700,
            'BANKNIFTY': -700,
            'FINNIFTY': -700,
            'MIDCPNIFTY': -250
        }
        return {
            'multiplier': multipliers.get(symbol, 1),
            'loss_limit': loss_limits.get(symbol, -500)
        }

    def store_trade_info(self, info: pd.DataFrame, file_path: str) -> None:
        """Store trade information to CSV"""
        try:
            info.to_csv(file_path, index=False)
            self.logger.info(f"Trade information stored in {file_path}")
        except Exception as e:
            self.logger.error(f"Error storing trade information: {e}")
            raise