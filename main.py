from flask import Flask, render_template, Response
from kiteconnect import KiteConnect
import datetime
import os
import time
from threading import Thread
import logging
from ratelimit import limits, sleep_and_retry
try:
    import pendulum
except ImportError:
    pendulum = None
try:
    import requests
except ImportError:
    requests = None

app = Flask(__name__, template_folder='templates')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Debug template folder
logger.info("Current working directory: %s", os.getcwd())
logger.info("Flask app root path: %s", app.root_path)
logger.info("Templates folder path: %s", os.path.join(app.root_path, 'templates'))
logger.info("Does templates folder exist? %s", os.path.exists(os.path.join(app.root_path, 'templates')))
logger.info("Does index.html exist? %s", os.path.exists(os.path.join(app.root_path, 'templates', 'index.html')))

# Your Kite Connect credentials from MoneyGarage
API_KEY = os.getenv("API_KEY", "c2wxelu2x0p4rtc")
API_SECRET = os.getenv("API_SECRET", "7ly65y73hzvcgsbnfqiugs1nzw73jzo")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # This will be set via environment variables

# Initialize Kite Connect
try:
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)
    logger.info("Kite Connect initialized successfully with API_KEY: %s and ACCESS_TOKEN: %s", API_KEY, ACCESS_TOKEN)
except Exception as e:
    logger.error("Error initializing Kite Connect: %s", e)

# Global variables for caching
app_active = False
last_updated = None
current_date_day = None
cached_data = None
cache_timestamp = None
CACHE_DURATION = 60  # Cache data for 60 seconds

# List of bank holidays in India for 2025
BANK_HOLIDAYS = [
    "2025-03-31",  # Bank Holiday
    "2025-04-10",  # Good Friday
    "2025-04-14",  # Dr. Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Janmashtami
    "2025-10-02",  # Gandhi Jayanti
    "2025-10-21",  # Diwali (Laxmi Pujan)
    "2025-10-22",  # Diwali (Balipratipada)
    "2025-11-05",  # Guru Nanak Jayanti
    "2025-12-25",  # Christmas
]

# List of BankNifty constituent stocks (symbols for Kite Connect API)
BANKNIFTY_STOCKS = [
    "NSE:HDFCBANK",
    "NSE:ICICIBANK",
    "NSE:SBIN",
    "NSE:KOTAKBANK",
    "NSE:AXISBANK",
    "NSE:BANKBARODA",
    "NSE:PNB",
    "NSE:CANBK",
    "NSE:INDUSINDBK",
    "NSE:FEDERALBNK",
    "NSE:IDFCFIRSTB",
    "NSE:AUBANK"  # AU Small Finance Bank
]

# Rate limiting for Kite Connect API (3 requests per second)
@sleep_and_retry
@limits(calls=3, period=1)  # 3 requests per second
def rate_limited_quote(symbols):
    try:
        response = kite.quote(symbols)
        logger.info("Rate limited quote response for symbols %s: %s", symbols, response)
        return response
    except Exception as e:
        logger.error("Error in rate_limited_quote for symbols %s: %s", symbols, e)
        return {}

# Function to get the last Thursday of the month, adjusting for bank holidays
def get_last_thursday_of_month(year, month):
    # Get the last day of the month
    last_day = (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)) if month < 12 else datetime.date(year, 12, 31)

    # Find the last Thursday
    current_date = last_day
    while current_date.weekday() != 3:  # 3 is Thursday
        current_date -= datetime.timedelta(days=1)

    # Check if the last Thursday is a bank holiday
    while current_date.strftime("%Y-%m-%d") in BANK_HOLIDAYS:
        current_date -= datetime.timedelta(days=1)  # Move to Wednesday if Thursday is a holiday
        while current_date.weekday() != 2:  # 2 is Wednesday
            current_date -= datetime.timedelta(days=1)

    return current_date

# Function to get the last Thursday of the week, adjusting for bank holidays
def get_last_thursday_of_week(start_date):
    # Find the end of the week (Saturday)
    days_to_saturday = (5 - start_date.weekday() + 7) % 7  # 5 is Saturday
    end_of_week = start_date + datetime.timedelta(days=days_to_saturday)

    # Find the last Thursday of the week
    current_date = end_of_week
    while current_date.weekday() != 3:  # 3 is Thursday
        current_date -= datetime.timedelta(days=1)

    # Check if the last Thursday is a bank holiday
    while current_date.strftime("%Y-%m-%d") in BANK_HOLIDAYS:
        current_date -= datetime.timedelta(days=1)  # Move to Wednesday if Thursday is a holiday
        while current_date.weekday() != 2:  # 2 is Wednesday
            current_date -= datetime.timedelta(days=1)

    return current_date

# Function to get the current monthly expiry for BankNifty
def get_banknifty_monthly_expiry():
    today = datetime.date.today()
    year = today.year
    month = today.month

    # If today is past the expiry date of the current month, move to the next month
    last_thursday = get_last_thursday_of_month(year, month)
    if today > last_thursday:
        month += 1
        if month > 12:
            month = 1
            year += 1
        last_thursday = get_last_thursday_of_month(year, month)

    return last_thursday

# Function to get the current weekly expiry for Nifty
def get_nifty_weekly_expiry():
    today = datetime.date.today()

    # Find the start of the week (Monday)
    start_of_week = today - datetime.timedelta(days=today.weekday())

    # Find the next Thursday (or Wednesday if Thursday is a holiday)
    expiry_date = get_last_thursday_of_week(start_of_week)

    # If today is past the expiry date, move to the next week
    if today > expiry_date:
        start_of_week += datetime.timedelta(days=7)
        expiry_date = get_last_thursday_of_week(start_of_week)

    return expiry_date

# Function to fetch ATM option contracts for summary
def get_atm_option_contracts():
    # Fetch all instruments
    instruments = kite.instruments("NFO")

    # Get current expiry dates
    nifty_expiry = get_nifty_weekly_expiry()
    banknifty_expiry = get_banknifty_monthly_expiry()

    # Find Nifty and BankNifty ATM options
    nifty_call = None
    nifty_put = None
    banknifty_call = None
    banknifty_put = None

    # Get spot prices to determine ATM strikes
    try:
        indices = rate_limited_quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
        logger.info("Indices quote response for ATM strikes: %s", indices)
        nifty_spot = indices["NSE:NIFTY 50"].get("last_price", 0)
        banknifty_spot = indices["NSE:NIFTY BANK"].get("last_price", 0)
    except Exception as e:
        logger.error("Error fetching spot prices for ATM strikes: %s", e)
        nifty_spot = 0
        banknifty_spot = 0

    # Round to nearest 100 for Nifty, 100 for BankNifty
    nifty_strike = round(nifty_spot / 100) * 100 if nifty_spot else 0
    banknifty_strike = round(banknifty_spot / 100) * 100 if banknifty_spot else 0

    for instrument in instruments:
        # Nifty Call and Put
        if instrument["expiry"] == nifty_expiry and instrument["name"] == "NIFTY" and instrument["strike"] == nifty_strike:
            if instrument["instrument_type"] == "CE":
                nifty_call = instrument["tradingsymbol"]
            elif instrument["instrument_type"] == "PE":
                nifty_put = instrument["tradingsymbol"]
        # BankNifty Call and Put
        if instrument["expiry"] == banknifty_expiry and instrument["name"] == "BANKNIFTY" and instrument["strike"] == banknifty_strike:
            if instrument["instrument_type"] == "CE":
                banknifty_call = instrument["tradingsymbol"]
            elif instrument["instrument_type"] == "PE":
                banknifty_put = instrument["tradingsymbol"]

    return nifty_call, nifty_put, banknifty_call, banknifty_put
# Function to fetch option chain for Nifty and BankNifty
def get_option_chain():
    # Fetch all instruments for NFO (futures and options)
    instruments = kite.instruments("NFO")

    # Get current expiry dates
    nifty_expiry = get_nifty_weekly_expiry()
    banknifty_expiry = get_banknifty_monthly_expiry()

    # Define strike ranges (reduced to minimize API calls)
    nifty_strike_range = range(23000, 24001, 100)  # Narrowed range to reduce symbols
    banknifty_strike_range = range(50000, 52001, 100)  # Narrowed range to reduce symbols

    # Collect option contracts
    nifty_options = {"calls": {}, "puts": {}}
    banknifty_options = {"calls": {}, "puts": {}}

    # Map strikes to trading symbols
    nifty_symbols = []
    banknifty_symbols = []

    for instrument in instruments:
        # Nifty options
        if instrument["expiry"] == nifty_expiry and instrument["name"] == "NIFTY" and instrument["strike"] in nifty_strike_range:
            if instrument["instrument_type"] == "CE":
                nifty_options["calls"][instrument["strike"]] = instrument["tradingsymbol"]
                nifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
            elif instrument["instrument_type"] == "PE":
                nifty_options["puts"][instrument["strike"]] = instrument["tradingsymbol"]
                nifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
        # BankNifty options
        if instrument["expiry"] == banknifty_expiry and instrument["name"] == "BANKNIFTY" and instrument["strike"] in banknifty_strike_range:
            if instrument["instrument_type"] == "CE":
                banknifty_options["calls"][instrument["strike"]] = instrument["tradingsymbol"]
                banknifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")
            elif instrument["instrument_type"] == "PE":
                banknifty_options["puts"][instrument["strike"]] = instrument["tradingsymbol"]
                banknifty_symbols.append(f"NFO:{instrument['tradingsymbol']}")

    # Fetch quotes for all option contracts in batches to avoid rate limits
    all_symbols = nifty_symbols + banknifty_symbols
    quotes = {}
    batch_size = 100  # Kite Connect allows up to 1000 symbols per request, but we use 100 to be safe
    for i in range(0, len(all_symbols), batch_size):
        batch = all_symbols[i:i + batch_size]
        try:
            batch_quotes = rate_limited_quote(batch)
            logger.info("Option chain batch quote response: %s", batch_quotes)
            quotes.update(batch_quotes)
        except Exception as e:
            logger.error("Error fetching option chain quotes for batch: %s", e)
            for symbol in batch:
                quotes[symbol] = {}

    # Process Nifty option chain
    nifty_chain = []
    for strike in nifty_strike_range:
        call_symbol = nifty_options["calls"].get(strike)
        put_symbol = nifty_options["puts"].get(strike)
        call_data = quotes.get(f"NFO:{call_symbol}", {})
        put_data = quotes.get(f"NFO:{put_symbol}", {})
        call_change = call_data.get("ohlc", {}).get("close", 0) and round(((call_data.get("last_price", 0) - call_data.get("ohlc", {}).get("close", 0)) / call_data.get("ohlc", {}).get("close", 0)) * 100, 2) if call_data.get("ohlc", {}).get("close", 0) else "N/A"
        put_change = put_data.get("ohlc", {}).get("close", 0) and round(((put_data.get("last_price", 0) - put_data.get("ohlc", {}).get("close", 0)) / put_data.get("ohlc", {}).get("close", 0)) * 100, 2) if put_data.get("ohlc", {}).get("close", 0) else "N/A"
        nifty_chain.append({
            "strike": strike,
            "call_oi": call_data.get("oi", "N/A"),
            "call_ltp": call_data.get("last_price", "N/A"),
            "call_volume": call_data.get("volume", "N/A"),
            "call_change": call_change,
            "put_oi": put_data.get("oi", "N/A"),
            "put_ltp": put_data.get("last_price", "N/A"),
            "put_volume": put_data.get("volume", "N/A"),
            "put_change": put_change
        })

    # Process BankNifty option chain
    banknifty_chain = []
    for strike in banknifty_strike_range:
        call_symbol = banknifty_options["calls"].get(strike)
        put_symbol = banknifty_options["puts"].get(strike)
        call_data = quotes.get(f"NFO:{call_symbol}", {})
        put_data = quotes.get(f"NFO:{put_symbol}", {})
        call_change = call_data.get("ohlc", {}).get("close", 0) and round(((call_data.get("last_price", 0) - call_data.get("ohlc", {}).get("close", 0)) / call_data.get("ohlc", {}).get("close", 0)) * 100, 2) if call_data.get("ohlc", {}).get("close", 0) else "N/A"
        put_change = put_data.get("ohlc", {}).get("close", 0) and round(((put_data.get("last_price", 0) - put_data.get("ohlc", {}).get("close", 0)) / put_data.get("ohlc", {}).get("close", 0)) * 100, 2) if put_data.get("ohlc", {}).get("close", 0) else "N/A"
        banknifty_chain.append({
            "strike": strike,
            "call_oi": call_data.get("oi", "N/A"),
            "call_ltp": call_data.get("last_price", "N/A"),
            "call_volume": call_data.get("volume", "N/A"),
            "call_change": call_change,
            "put_oi": put_data.get("oi", "N/A"),
            "put_ltp": put_data.get("last_price", "N/A"),
            "put_volume": put_data.get("volume", "N/A"),
            "put_change": put_change
        })

    return nifty_chain, banknifty_chain

# Function to calculate VWAP from historical data
def calculate_vwap(historical_data):
    if not historical_data:
        return "VWAP Unavailable"

    total_price_volume = 0
    total_volume = 0

    for data_point in historical_data:
        # Calculate typical price: (High + Low + Close) / 3
        typical_price = (data_point["high"] + data_point["low"] + data_point["close"]) / 3
        volume = data_point["volume"]
        total_price_volume += typical_price * volume
        total_volume += volume

    if total_volume == 0:
        return "VWAP Unavailable"

    vwap = total_price_volume / total_volume
    return round(vwap, 2)

# Function to fetch Nifty and BankNifty futures data for the current month
def get_futures_data():
    # Get the current month and year
    today = datetime.date.today()
    year = today.year
    month = today.month

    # Get the last Thursday of the current month for futures expiry
    futures_expiry = get_last_thursday_of_month(year, month)

    # If today is past the expiry, move to the next month
    if today > futures_expiry:
        month += 1
        if month > 12:
            month = 1
            year += 1
        futures_expiry = get_last_thursday_of_month(year, month)

    # Format the expiry for the trading symbol (e.g., "25APR" for April 2025)
    expiry_str = futures_expiry.strftime("%y%b").upper()  # e.g., "25APR"

    # Define futures symbols
    nifty_future_symbol = f"NFO:NIFTY{expiry_str}FUT"
    banknifty_future_symbol = f"NFO:BANKNIFTY{expiry_str}FUT"

    # Fetch instrument tokens for historical data
    instruments = kite.instruments("NFO")
    nifty_instrument_token = None
    banknifty_instrument_token = None

    for instrument in instruments:
        if instrument["tradingsymbol"] == f"NIFTY{expiry_str}FUT":
            nifty_instrument_token = instrument["instrument_token"]
        if instrument["tradingsymbol"] == f"BANKNIFTY{expiry_str}FUT":
            banknifty_instrument_token = instrument["instrument_token"]

    # Fetch futures data
    try:
        futures_data = rate_limited_quote([nifty_future_symbol, banknifty_future_symbol])
        logger.info("Futures quote response: %s", futures_data)
        nifty_future = futures_data.get(nifty_future_symbol, {})
        banknifty_future = futures_data.get(banknifty_future_symbol, {})

        # Fallback to current IST time if last_time is missing
        nifty_timestamp = nifty_future.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        banknifty_timestamp = banknifty_future.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Fetch historical data to calculate VWAP
        # Define the time range: from market open (9:15 AM IST) to current time
        now = datetime.datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now < market_open:
            market_open = market_open - datetime.timedelta(days=1)  # Use previous day's open if before 9:15 AM

        # Fetch 1-minute historical data for Nifty futures
        nifty_vwap = "VWAP Unavailable"
        if nifty_instrument_token:
            try:
                historical_data = kite.historical_data(
                    instrument_token=nifty_instrument_token,
                    from_date=market_open,
                    to_date=now,
                    interval="minute"
                )
                logger.info("Nifty futures historical data: %s", historical_data)
                nifty_vwap = calculate_vwap(historical_data)
            except Exception as e:
                logger.error("Error fetching historical data for Nifty futures: %s", e)

        # Fetch 1-minute historical data for BankNifty futures
        banknifty_vwap = "VWAP Unavailable"
        if banknifty_instrument_token:
            try:
                historical_data = kite.historical_data(
                    instrument_token=banknifty_instrument_token,
                    from_date=market_open,
                    to_date=now,
                    interval="minute"
                )
                logger.info("BankNifty futures historical data: %s", historical_data)
                banknifty_vwap = calculate_vwap(historical_data)
            except Exception as e:
                logger.error("Error fetching historical data for BankNifty futures: %s", e)

        return {
            "nifty_future": {
                "ltp": nifty_future.get("last_price", "N/A"),
                "timestamp": nifty_timestamp,
                "vwap": nifty_vwap
            },
            "banknifty_future": {
                "ltp": banknifty_future.get("last_price", "N/A"),
                "timestamp": banknifty_timestamp,
                "vwap": banknifty_vwap
            }
        }
    except Exception as e:
        logger.error("Error fetching futures data: %s", e)
        return {
            "nifty_future": {"ltp": "N/A", "timestamp": "Timestamp Unavailable", "vwap": "VWAP Unavailable"},
            "banknifty_future": {"ltp": "N/A", "timestamp": "Timestamp Unavailable", "vwap": "VWAP Unavailable"}
        }

# Function to fetch BankNifty constituent stocks' LTP, % change, and volume
def get_bank_stocks_data():
    try:
        quotes = rate_limited_quote(BANKNIFTY_STOCKS)
        logger.info("Bank stocks quote response: %s", quotes)
        bank_stocks = []
        for symbol in BANKNIFTY_STOCKS:
            stock_data = quotes.get(symbol, {})
            ltp = stock_data.get("last_price", "N/A")
            close = stock_data.get("ohlc", {}).get("close", 0)
            change_percent = round(((ltp - close) / close) * 100, 2) if close and ltp != "N/A" else "N/A"
            volume = stock_data.get("volume", "N/A")
            bank_stocks.append({
                "name": symbol.split(":")[1],  # Extract stock name (e.g., HDFCBANK)
                "ltp": ltp,
                "change_percent": change_percent,
                "volume": volume
            })
        # Sort into gainers and losers
        gainers = sorted([stock for stock in bank_stocks if isinstance(stock["change_percent"], (int, float)) and stock["change_percent"] >= 0], key=lambda x: x["change_percent"], reverse=True)
        losers = sorted([stock for stock in bank_stocks if isinstance(stock["change_percent"], (int, float)) and stock["change_percent"] < 0], key=lambda x: x["change_percent"])
        return gainers, losers
    except Exception as e:
        logger.error("Error fetching bank stocks data: %s", e)
        return [], []

# Function to fetch all required data
def get_indices_data():
    global cached_data, cache_timestamp

    # Check if cached data is still valid
    if cache_timestamp and (time.time() - cache_timestamp) < CACHE_DURATION and cached_data:
        logger.info("Returning cached data")
        return cached_data

    try:
        # Fetch Indices data (Nifty 50, BankNifty, India VIX, Sensex, Nifty Midcap)
        indices_symbols = ["NSE:NIFTY 50", "NSE:NIFTY BANK", "NSE:INDIA VIX", "BSE:SENSEX", "NSE:NIFTY MIDCAP 50"]
        indices = rate_limited_quote(indices_symbols)
        logger.info("Indices quote response: %s", indices)
        nifty = indices["NSE:NIFTY 50"]
        banknifty = indices["NSE:NIFTY BANK"]
        india_vix = indices["NSE:INDIA VIX"]
        sensex = indices["BSE:SENSEX"]
        nifty_midcap = indices["NSE:NIFTY MIDCAP 50"]

        # Fetch futures data for Nifty and BankNifty (to get VWAP)
        futures = get_futures_data()

        # Fetch ATM OI data for Nifty and BankNifty
        nifty_call_symbol, nifty_put_symbol, banknifty_call_symbol, banknifty_put_symbol = get_atm_option_contracts()
        option_symbols = [f"NFO:{symbol}" for symbol in [nifty_call_symbol, nifty_put_symbol, banknifty_call_symbol, banknifty_put_symbol] if symbol]
        try:
            options_data = rate_limited_quote(option_symbols) if option_symbols else {}
            logger.info("Options quote response: %s", options_data)
        except Exception as e:
            logger.error("Error fetching options data: %s", e)
            options_data = {}

        # Fetch option chain data
        nifty_chain, banknifty_chain = get_option_chain()

        # Fetch BankNifty constituent stocks data
        bank_stocks_gainers, bank_stocks_losers = get_bank_stocks_data()

        # Fallback to current IST time if last_time is missing
        nifty_timestamp = nifty.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        banknifty_timestamp = banknifty.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        india_vix_timestamp = india_vix.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        sensex_timestamp = sensex.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        nifty_midcap_timestamp = nifty_midcap.get("last_time", pendulum.now('Asia/Kolkata').strftime("%Y-%m-%d %H:%M:%S") if pendulum else datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # Use futures VWAP (calculated manually)
        nifty_vwap = futures["nifty_future"].get("vwap", "VWAP Unavailable")
        banknifty_vwap = futures["banknifty_future"].get("vwap", "VWAP Unavailable")

        # Prepare the data dictionary
        data = {
            "current_date_day": current_date_day,
            "last_updated": last_updated,
            "nifty": {
                "last_price": nifty.get("last_price", "N/A"),
                "timestamp": nifty_timestamp,
                "vwap": nifty_vwap
            },
            "banknifty": {
                "last_price": banknifty.get("last_price", "N/A"),
                "timestamp": banknifty_timestamp,
                "vwap": banknifty_vwap
            },
            "india_vix": {
                "last_price": india_vix.get("last_price", "N/A"),
                "timestamp": india_vix_timestamp,
                "vwap": "N/A"  # VWAP not applicable for India VIX
            },
            "sensex": {
                "last_price": sensex.get("last_price", "N/A"),
                "timestamp": sensex_timestamp,
                "vwap": "N/A"  # VWAP not applicable for Sensex
            },
            "nifty_midcap": {
                "last_price": nifty_midcap.get("last_price", "N/A"),
                "timestamp": nifty_midcap_timestamp,
                "vwap": "N/A"  # VWAP not applicable for Nifty Midcap
            },
            "futures": futures,
            "options": {
                "nifty_call": options_data.get(f"NFO:{nifty_call_symbol}", {}).get("oi", "N/A"),
                "nifty_put": options_data.get(f"NFO:{nifty_put_symbol}", {}).get("oi", "N/A"),
                "banknifty_call": options_data.get(f"NFO:{banknifty_call_symbol}", {}).get("oi", "N/A"),
                "banknifty_put": options_data.get(f"NFO:{banknifty_put_symbol}", {}).get("oi", "N/A")
            },
            "nifty_chain": nifty_chain,
            "banknifty_chain": banknifty_chain,
            "bank_stocks_gainers": bank_stocks_gainers,
            "bank_stocks_losers": bank_stocks_losers
        }

        # Cache the data
        cached_data = data
        cache_timestamp = time.time()
        return data
    except Exception as e:
        logger.error("Error fetching indices data: %s", e)
        return {"error": f"Failed to fetch data: {str(e)}"}

# Function to check if current time is within market hours
def is_within_market_hours():
    # Try using pendulum for more reliable time zone handling
    if pendulum:
        try:
            ist_now = pendulum.now('Asia/Kolkata')
            logger.info("Using pendulum - IST time: %s", ist_now)
        except Exception as e:
            logger.error("Error using pendulum: %s", e)
            ist_now = None
    else:
        ist_now = None

    # Fallback to datetime if pendulum is not available or fails
    if not ist_now:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        ist_now = utc_now + ist_offset
        logger.info("Using datetime - UTC time: %s", utc_now)
        logger.info("Using datetime - IST time: %s", ist_now)

    # Fallback to worldtimeapi.org if both methods fail
    if not ist_now or (ist_now.hour < 9 or ist_now.hour > 15):
        if requests:
            try:
                response = requests.get("http://worldtimeapi.org/api/timezone/Asia/Kolkata")
                response.raise_for_status()
                time_data = response.json()
                ist_now = pendulum.parse(time_data["datetime"])
                logger.info("Using worldtimeapi - IST time: %s", ist_now)
            except Exception as e:
                logger.error("Error fetching time from worldtimeapi: %s", e)
                # If all methods fail, fall back to datetime
                utc_now = datetime.datetime.now(datetime.timezone.utc)
                ist_offset = datetime.timedelta(hours=5, minutes=30)
                ist_now = utc_now + ist_offset
                logger.info("Fallback to datetime - UTC time: %s", utc_now)
                logger.info("Fallback to datetime - IST time: %s", ist_now)

    # Get current day and time
    current_day = ist_now.weekday()  # 0 = Monday, 6 = Sunday
    current_hour = ist_now.hour
    current_minute = ist_now.minute
    current_date = ist_now.strftime("%Y-%m-%d")

    # Check if today is a bank holiday
    if current_date in BANK_HOLIDAYS:
        logger.info("Today (%s) is a bank holiday", current_date)
        return False

    # Market hours: 9:15 AM to 3:30 PM IST, Monday to Friday
    is_weekday = 0 <= current_day <= 4  # Monday to Friday
    is_after_open = (current_hour > 9) or (current_hour == 9 and current_minute >= 15)
    is_before_close = (current_hour < 15) or (current_hour == 15 and current_minute < 30)

    logger.info("is_weekday: %s, is_after_open: %s, is_before_close: %s", is_weekday, is_after_open, is_before_close)
    return is_weekday and is_after_open and is_before_close

# Function to update app status and timestamps
def update_app_status():
    global app_active, last_updated, current_date_day
    while True:
        app_active = is_within_market_hours()

        # Update timestamps
        if pendulum:
            try:
                ist_now = pendulum.now('Asia/Kolkata')
            except Exception as e:
                logger.error("Error using pendulum in update_app_status: %s", e)
                utc_now = datetime.datetime.now(datetime.timezone.utc)
                ist_offset = datetime.timedelta(hours=5, minutes=30)
                ist_now = utc_now + ist_offset
        else:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            ist_offset = datetime.timedelta(hours=5, minutes=30)
            ist_now = utc_now + ist_offset

        last_updated = ist_now.strftime("%Y-%m-%d %H:%M:%S IST")
        current_date_day = ist_now.strftime("%Y-%m-%d, %A")

        logger.info("App active status: %s, Last updated: %s", app_active, last_updated)
        time.sleep(60)  # Check every minute

# Start the status updater in a separate thread
status_thread = Thread(target=update_app_status, daemon=True)
status_thread.start()

# Health check endpoint for Render
@app.route('/health')
def health_check():
    if app_active:
        return "OK", 200
    else:
        return "Outside market hours", 503

# Route for the webpage
@app.route('/')
def display_indices():
    if not app_active:
        return "App is outside market hours (9:15 AM to 3:30 PM IST, Monday to Friday).", 503

    # Prevent browser caching
    response = Response(render_template('index.html', data=get_indices_data()))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

if __name__ == '__main__':
    port = int(os.getenv("PORT", 8080))
    app.run(host='0.0.0.0', port=port)