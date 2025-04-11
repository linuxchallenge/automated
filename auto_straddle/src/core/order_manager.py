# src/core/order_manager.py

from typing import Dict, Tuple, Optional
from datetime import datetime
import asyncio
import logging

class OrderManager:
    def __init__(self, broker_client):
        self.broker = broker_client
        self.logger = logging.getLogger(self.__class__.__name__)
        self.orders: Dict[str, Dict] = {}

    async def place_order(self, 
                         account: str,
                         symbol: str,
                         strike: float,
                         option_type: str,
                         quantity: int) -> str:
        """Place a new order"""
        try:
            order_id = await self.broker.place_orders(
                account=account,
                strike=strike,
                option_type=option_type,
                symbol=symbol,
                quantity=quantity
            )
            
            if order_id == -1:
                raise Exception(f"Order placement failed for {symbol} {strike} {option_type}")

            self.orders[order_id] = {
                'account': account,
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'quantity': quantity,
                'status': 'PENDING',
                'time': datetime.now()
            }
            return order_id

        except Exception as e:
            self.logger.error(f"Order placement error: {str(e)}")
            raise

    async def get_order_status(self, 
                              account: str,
                              order_id: str,
                              expected_price: float) -> Tuple[str, float]:
        """Get the status of an order"""
        try:
            status, price = await self.broker.order_status(
                account, 
                order_id,
                expected_price
            )
            
            if order_id in self.orders:
                self.orders[order_id]['status'] = status
                self.orders[order_id]['executed_price'] = price

            return status, price

        except Exception as e:
            self.logger.error(f"Order status check error: {str(e)}")
            raise