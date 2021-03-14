import os
import sys
import ccxt
import json
import time
from datetime import datetime
import requests
import traceback
import bybit as bybit_official

args = sys.argv


class Param:
    def __init__(self, param: dict):
        self.symbol = param["symbol"]
        self.sleep_time = param["sleep_time"]
        self.order_unit = param["order_unit"]
        self.leverage = param["leverage"]
        self.target_price = param["target_price"]
        self.start_balance = param["start_balance"]


class TickerInfo:
    def __init__(self, ticker: dict):
        self.bid = ticker["bid"]
        self.ask = ticker["ask"]
        self.last = ticker["last"]
        self.vwap = ticker["vwap"]
        self.funding_rate = ticker["info"]["funding_rate"]


class Order:
    def __init__(self, bybit: ccxt.bybit):
        self.bybit = bybit

    def market_buy_order(self, symbol: str, amount: float):
        result = self.bybit.create_market_buy_order(symbol=symbol, amount=amount)
        return result

    def market_sell_order(self, symbol: str, amount: float):
        result = self.bybit.create_market_sell_order(symbol=symbol, amount=amount)
        return result

    def limit_buy_order(self, symbol: str, amount: float, price: float, postOnly: bool = False):
        if postOnly:
            params = {"time_in_force": "PostOnly"}
        else:
            params = {}
        result = self.bybit.create_limit_buy_order(symbol=symbol, amount=amount, price=price, params=params)
        return result

    def limit_sell_order(self, symbol: str, amount: float, price: float, postOnly: bool = False):
        if postOnly:
            params = {"time_in_force": "PostOnly"}
        else:
            params = {}
        result = self.bybit.create_limit_sell_order(symbol=symbol, amount=amount, price=price, params=params)
        return result


class CustomLog:
    def __init__(self, record_log: bool = False, without_time: bool = False, record_path: str = None):
        self.record_log = record_log
        self.without_time = without_time
        self.record_path = record_path

    def print_log(self, text):
        now_time = datetime.now()
        time_text = f"[{now_time.strftime('%Y-%m-%d %H:%M:%S.%f')}]"
        if self.without_time:
            p_text = f'{text}'
        else:
            p_text = f'{time_text} {text}'
        print(p_text)

        if self.record_log and not ((self.record_path in [None, ""]) or (" " in self.record_path)):
            self.write_log(text=p_text)

    def write_log(self, text: str):
        path_struct = os.path.split(self.record_path)
        if not os.path.exists(path_struct[0]):
            os.mkdir(path_struct[0])

        with open(self.record_path, 'a') as f:
            print(text, file=f)


class LineNotify:
    def __init__(self, api_key: str, logger: CustomLog):
        self.api_key = api_key
        self.logger = logger

    def line_notify(self, message: str, pic=False, path=None):
        try:
            line_notify_api = 'https://notify-api.line.me/api/notify'
            message = "\n" + message
            payload = {'message': message}

            if not pic:
                headers = {'Authorization': 'Bearer ' + self.api_key}
                requests.post(line_notify_api, data=payload, headers=headers)
            else:
                files = {"imageFile": open(path, "rb")}
                headers = {'Authorization': 'Bearer ' + self.api_key}
                requests.post(line_notify_api, data=payload, headers=headers, files=files)
        except Exception as e:
            self.logger.print_log(f"Error: {e}")
            print(traceback.format_exc())
            self.line_notify(message=f"Error: {e}")
            self.line_notify(traceback.format_exc())


def print_json(data: dict, indent: int = 4):
    print(json.dumps(data, indent=indent))


def cancel_info(cancel_info: dict, logger: CustomLog):
    msg = f"[Cancel Order] id: {cancel_info['info']['clOrdID']}, symbol: {cancel_info['info']['symbol']}, side: {cancel_info['info']['side']}, amount: {cancel_info['info']['qty']}"
    logger.print_log(text=msg)
    return msg


def order_info(order_info: dict, logger: CustomLog):
    if order_info['info']['side'] == "Buy":
        msg = f"[New Buy Order]  id: {order_info['info']['order_id']}, symbol: {order_info['info']['symbol']}, type: {order_info['info']['order_type']}, side: {order_info['info']['side']}, amount: {order_info['info']['qty']}"
        logger.print_log(text=msg)
        return msg
    elif order_info['info']['side'] == "Sell":
        msg = f"[New Sell Order] id: {order_info['info']['order_id']}, symbol: {order_info['info']['symbol']}, type: {order_info['info']['order_type']}, side: {order_info['info']['side']}, amount: {order_info['info']['qty']}"
        logger.print_log(text=msg)
        return msg


def get_position(bybit_sdk: bybit_official.bybit, symbol: str):
    position_info = bybit_sdk.Positions.Positions_myPosition(symbol=symbol.replace("/", "")).result()[0]
    if position_info["result"]["side"] == "Buy":
        return position_info["result"]["size"]
    elif position_info["result"]["side"] == "Sell":
        return -1 * position_info["result"]["size"]
    else:
        return 0


def get_roe(before: float, after: float):
    return (after - before) / before


def main(bybit: ccxt.bybit, param_path: str, notify_key: str, log_path: str):
    config_open = open(param_path, "r")
    param_json = json.load(config_open)
    param = Param(param=param_json)
    order = Order(bybit=bybit)
    logger = CustomLog(record_log=True, record_path=log_path)
    notify = LineNotify(api_key=notify_key, logger=logger)
    bybit_sdk = bybit_official.bybit(test=False, api_key=bybit.apiKey, api_secret=bybit.secret)
    base_currency = param.symbol.split('/')[0]

    # Cancel all open orders
    cancel_all_order = bybit.cancel_all_orders(symbol=param.symbol)
    for info in cancel_all_order:
        msg = cancel_info(cancel_info=info, logger=logger)
        msg = f"{param.symbol}\n" + msg
        notify.line_notify(msg)

    while True:
        try:
            config_open = open(param_path, "r")
            param_json = json.load(config_open)
            param = Param(param=param_json)

            ticker = TickerInfo(ticker=bybit.fetch_ticker(symbol=param.symbol))
            balance = bybit.fetch_balance()["total"][base_currency]
            position = get_position(bybit_sdk=bybit_sdk, symbol=param.symbol)

            # If there has no position, make a long position of 'leverage' times your start balance.
            if position == 0:
                lot = int(balance * ticker.last * param.leverage)
                order_result = order.market_buy_order(symbol=param.symbol, amount=lot)
                msg = order_info(order_info=order_result, logger=logger)
                notify.line_notify(message=msg)
                position += lot
            else:
                # Close all positions when the target price is reached.
                if ticker.last >= param.target_price:
                    order_result = order.market_sell_order(symbol=param.symbol, amount=position)
                    msg = order_info(order_info=order_result, logger=logger)
                    notify.line_notify(message=msg)

                    time.sleep(5)
                    balance = bybit.fetch_balance()["total"][base_currency]

                    msg = "Reached target price.\n"
                    msg += f"Balance: {balance} {base_currency}"
                    logger.print_log(text=msg)
                    msg = f"{param.symbol}\n" + msg
                    notify.line_notify(message=msg)
                    exit(0)

                # If the leverage ratio is less than the specified leverage ratio after placing an order for
                # 'order_unit', the order for 'order_unit' will be placed.
                if (position + param.order_unit) / (ticker.last * balance) <= param.leverage:
                    lot = param.order_unit
                    order_result = order.market_buy_order(symbol=param.symbol, amount=lot)
                    msg = order_info(order_info=order_result, logger=logger)
                    msg = f"{param.symbol}\n" + msg
                    notify.line_notify(message=msg)
                    position += lot

            # balance = bybit.fetch_balance()["total"][base_currency]
            # position = get_position(bybit_sdk=bybit_sdk, symbol=param.symbol)
            real_leverage = position / (balance * ticker.last)

            # Record account and position information.
            msg = f"Price: {ticker.last} | " + f"Position: {position} USD | " + f"Balance: {balance} {base_currency} | " \
                  + f"ROE: {round(get_roe(param.start_balance, balance) * 100, 3)}% | " + f"Effective Leverage: {round(real_leverage, 3)}x"
            logger.print_log(text=msg)

        except Exception as e:
            error_msg = f"Error: {e}\n{traceback.format_exc()}"
            logger.print_log(text=error_msg)
            error_msg = f"{param.symbol}\n" + error_msg
            notify.line_notify(message=error_msg)

        time.sleep(param.sleep_time)


if __name__ == '__main__':
    config_open = open("./api_info.json", "r")
    api_info = json.load(config_open)
    bybit = ccxt.bybit()
    bybit.apiKey = api_info["api_key"]
    bybit.secret = api_info["api_secret"]
    line_notify_key = api_info["line_notify_key"]
    param_path = args[1]
    log_path = args[2]
    main(bybit=bybit, param_path=param_path, log_path=log_path, notify_key=line_notify_key)
