import unittest
from AutoStraddleStrategy import AutoStraddleStrategy
from OptionChainData import OptionChainData
from OptionChainData import UnderlyingSymbol

class TestAutoStraddleStrategy(unittest.TestCase):

    def test_basic_second(self):
        # Example usage:
        accounts = ["Account1", "Account2"]
        symbols = [UnderlyingSymbol.NIFTY, UnderlyingSymbol.BANKNIFTY, UnderlyingSymbol.FINNIFTY]

        for symbol in symbols:
            option_chain_analyzer = OptionChainData(symbol)

            # If symbol is nifty, use the following line to get the option chain data
            option_chain_info = option_chain_analyzer.get_option_chain_info(strike_data=0)

            if option_chain_info is not None:
                auto_straddle_strategy = AutoStraddleStrategy(accounts, symbols)
                auto_straddle_strategy.execute_strategy(option_chain_info, symbol, "Account1")
            else:
                print(f"Option chain information not available for symbol {symbol}")


if __name__ == '__main__':
    unittest.main()